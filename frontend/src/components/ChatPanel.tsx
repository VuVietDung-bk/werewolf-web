import { useEffect, useState, type FormEvent } from 'react'
import { api, jsonBody, type ChatChannel, type ChatContext, type ChatMessage, type ChatPage, type ChatPolicy, type Room } from '../api'
import { useRequestIds } from '../useRequestIds'
import { useConfirmation } from '../useConfirmation'

const channelNames: Record<ChatChannel, string> = {
  common: 'Chat chung', team: 'Chat đội', host: 'Chat quản trò',
}

function mergeMessages(previous: ChatMessage[], next: ChatMessage[]): ChatMessage[] {
  const byId = new Map(previous.map((message) => [message.id, message]))
  for (const message of next) byId.set(message.id, message)
  return [...byId.values()].sort((a, b) => a.sequence - b.sequence)
}

export default function ChatPanel({ room }: { room: Room }) {
  const [channel, setChannel] = useState<ChatChannel>('common')
  const [context, setContext] = useState<ChatContext | null>(null)
  const [policyDraft, setPolicyDraft] = useState<ChatPolicy | null>(null)
  const [policyReason, setPolicyReason] = useState('')
  const [teamLabel, setTeamLabel] = useState('')
  const [participantId, setParticipantId] = useState(room.members[0]?.user_id ?? '')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [before, setBefore] = useState<number | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [error, setError] = useState('')
  const requestIdFor = useRequestIds()
  const { ask, confirmationDialog } = useConfirmation()
  const base = `/api/rooms/${room.id}/chat`

  useEffect(() => {
    let active = true
    async function refreshContext() {
      try {
        const next = await api<ChatContext>(`${base}/context`)
        if (!active) return
        setContext(next)
        setPolicyDraft((current) => current ?? next.policy)
        setTeamLabel((current) => current || next.current_team_label || next.team_labels[0] || '')
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : 'Không tải được quyền chat.')
      }
    }
    void refreshContext()
    const timer = window.setInterval(() => void refreshContext(), 6000)
    return () => { active = false; window.clearInterval(timer) }
  }, [base])

  useEffect(() => {
    if (context && channel === 'team' && context.team_labels.length === 0) setChannel('common')
  }, [channel, context])

  const selectedParticipant = room.is_host ? participantId : undefined
  const selectedTeam = room.is_host && channel === 'team' ? teamLabel : undefined
  const canRead = channel !== 'host' || !room.is_host || Boolean(selectedParticipant)

  function query(beforeSequence?: number, afterSequence?: number): string {
    const params = new URLSearchParams({ channel })
    if (selectedTeam) params.set('team_label', selectedTeam)
    if (channel === 'host' && selectedParticipant) params.set('participant_user_id', selectedParticipant)
    if (beforeSequence) params.set('before_sequence', String(beforeSequence))
    if (afterSequence !== undefined) params.set('after_sequence', String(afterSequence))
    return `${base}/messages?${params}`
  }

  useEffect(() => {
    let active = true
    let first = true
    let latestSequence = 0
    setMessages([])
    setBefore(null)
    setError('')
    if (!canRead) return () => { active = false }
    async function refresh() {
      try {
        if (first) {
          const page = await api<ChatPage>(query())
          if (!active) return
          setMessages(page.messages)
          setBefore(page.next_before_sequence)
          latestSequence = page.messages.at(-1)?.sequence ?? 0
          first = false
          return
        }
        let cursor = latestSequence
        while (true) {
          const page: ChatPage = await api<ChatPage>(query(undefined, cursor))
          if (!active) return
          if (page.messages.length) {
            setMessages((current) => mergeMessages(current, page.messages))
            latestSequence = page.messages.at(-1)!.sequence
          }
          if (page.next_after_sequence === null) break
          cursor = page.next_after_sequence
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : 'Không tải được chat.')
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [base, channel, selectedParticipant, selectedTeam, canRead])

  async function loadOlder() {
    if (before === null || loadingOlder) return
    setLoadingOlder(true)
    try {
      const page = await api<ChatPage>(query(before))
      setMessages((current) => mergeMessages(current, page.messages))
      setBefore(page.next_before_sequence)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không tải được tin cũ.')
    } finally { setLoadingOlder(false) }
  }

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !text.trim()) return
    const payload = { channel, text: text.trim(),
      ...(channel === 'team' && room.is_host ? { team_label: teamLabel } : {}),
      ...(channel === 'host' && room.is_host ? { participant_user_id: participantId } : {}) }
    const request = requestIdFor({ room_id: room.id, payload })
    setBusy(true)
    setError('')
    void (async () => {
      let sent = false
      try {
        await api(`${base}/messages`, { method: 'POST', body: jsonBody({ ...payload, client_request_id: request.id }) })
        sent = true
        request.acknowledge()
        setText('')
        const page = await api<ChatPage>(query())
        setMessages((current) => mergeMessages(current, page.messages))
      } catch (cause) {
        setError(sent ? 'Tin đã gửi, nhưng chưa tải lại được. Vui lòng mở lại tab Chat để xem.'
          : cause instanceof Error ? cause.message : 'Không gửi được tin.')
      } finally { setBusy(false) }
    })()
  }

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!policyDraft || !policyReason.trim() || busy) return
    const summary = [
      `Chat chung ban đêm: ${policyDraft.common_send_at_night ? 'cho gửi' : 'chỉ đọc'}`,
      `Chat đội ban ngày: ${policyDraft.team_send_in_day ? 'cho gửi' : 'chỉ đọc'}`,
      `Người chết gửi chat chung: ${policyDraft.dead_common_send ? 'có' : 'không'}`,
      `Người chết gửi chat đội: ${policyDraft.dead_team_send ? 'có' : 'không'}`,
    ].join(' · ')
    const confirmed = await ask({ title: 'Đổi quyền chat',
      phase: room.status === 'playing' ? `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}` : 'Trước trận',
      subject: room.name, audience: 'Toàn phòng', publicText: summary,
      note: `Lý do: ${policyReason.trim()}. Quyền mới có hiệu lực ngay và được lưu vào sự kiện.`,
      confirmLabel: 'Lưu quyền chat' })
    if (!confirmed) return
    const payload = { ...policyDraft, reason: policyReason.trim() }
    const request = requestIdFor({ room_id: room.id, policy: payload })
    setBusy(true)
    setError('')
    let saved = false
    try {
      await api(`${base}/policy`, { method: 'PATCH', body: jsonBody({ ...payload, client_request_id: request.id }) })
      saved = true
      request.acknowledge()
      const next = await api<ChatContext>(`${base}/context`)
      setContext(next)
      setPolicyDraft(next.policy)
      setPolicyReason('')
    } catch (cause) {
      setError(saved ? 'Quyền chat đã lưu, nhưng chưa tải lại được. Vui lòng mở lại tab Chat để xem.'
        : cause instanceof Error ? cause.message : 'Không lưu được quyền chat.')
    } finally { setBusy(false) }
  }

  const canSend = channel === 'common' ? context?.can_send_common
    : channel === 'team' ? context?.can_send_team && (!room.is_host || context.writable_team_labels.includes(teamLabel))
      : context?.can_send_host && (!room.is_host || Boolean(participantId))

  return <section className="chat-panel" aria-labelledby="chat-title">
    <span className="eyebrow">CHUYỆN TRONG LÀNG</span><h2 id="chat-title">Chat</h2>
    <p>Chat đội và chat với quản trò chỉ hiện cho người có quyền trong trận. Sau trận, toàn bộ chat được công bố cho người từng tham gia.</p>
    {context && <p className="chat-policy-summary">Quyền hiện tại: chat chung ban đêm {context.policy.common_send_at_night ? 'được gửi' : 'chỉ đọc'}; chat đội ban ngày {context.policy.team_send_in_day ? 'được gửi' : 'chỉ đọc'}; người chết {context.policy.dead_common_send ? 'được gửi chat chung' : 'chỉ đọc chat chung'} và {context.policy.dead_team_send ? 'được tham gia chat đội' : 'chỉ đọc tin đội cũ'}.</p>}
    <div className="chat-channel-tabs" role="group" aria-label="Kênh chat">
      {(['common', ...(context?.team_labels.length ? ['team'] : []), 'host'] as ChatChannel[]).map((item) => <button key={item} type="button"
        className={channel === item ? 'active' : ''} aria-pressed={channel === item}
        onClick={() => { setChannel(item); setText('') }}>{channelNames[item]}</button>)}
    </div>
    {channel === 'team' && room.is_host && <label className="chat-picker">Đội chat
      <select value={teamLabel} onChange={(event) => setTeamLabel(event.target.value)}>
        {context?.team_labels.length ? context.team_labels.map((label) => <option key={label} value={label}>{label}{context.writable_team_labels.includes(label) ? '' : ' · lịch sử'}</option>)
          : <option value="">Chưa có đội chat</option>}
      </select></label>}
    {channel === 'host' && room.is_host && <label className="chat-picker">Người chơi
      <select value={participantId} onChange={(event) => setParticipantId(event.target.value)}>
        {room.members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
      </select></label>}
    {channel === 'team' && !room.is_host && <p className="chat-scope">Đội hiện tại: {context?.current_team_label || 'Không có'} · Tin của các đội bạn từng tham gia vẫn đọc được.</p>}
    {channel === 'host' && !room.is_host && <p className="chat-scope">Chỉ bạn và quản trò thấy cuộc trò chuyện này trong trận.</p>}
    {before !== null && <button type="button" className="button-secondary chat-older" disabled={loadingOlder}
      onClick={() => void loadOlder()}>{loadingOlder ? 'Đang tải…' : 'Xem tin cũ hơn'}</button>}
    <ol className="chat-message-list" aria-live="polite">
      {messages.map((message) => <li key={message.id}>
        <div><strong>{message.sender_user_id === room.host_id ? 'Quản trò' : message.sender_name}</strong>
          {channel === 'team' && message.team_label && <span> · {message.team_label}</span>}
          <time dateTime={message.created_at}>{new Date(message.created_at).toLocaleString('vi-VN')}</time></div>
        <p>{message.text}</p>
      </li>)}
    </ol>
    {!messages.length && <p className="chat-empty">Chưa có tin nhắn ở kênh này.</p>}
    {error && <p className="form-message" role="alert">{error}</p>}
    {canSend ? <form className="chat-send" onSubmit={send}>
      <label htmlFor="chat-text">Gửi vào {channelNames[channel].toLowerCase()}</label>
      <textarea id="chat-text" maxLength={2000} value={text} onChange={(event) => setText(event.target.value)} />
      <button disabled={busy || !text.trim()}>{busy ? 'Đang gửi…' : 'Gửi tin'}</button>
    </form> : <p className="chat-scope">Hiện tại bạn chỉ có thể đọc kênh này.</p>}
    {room.is_host && room.status !== 'finished' && policyDraft && <form className="chat-policy" onSubmit={(event) => void savePolicy(event)}>
      <h3>Chính sách chat của phòng</h3>
      <p>Trạng thái tạm dừng luôn chặn gửi tin. Quyền nhắn quản trò vẫn do từng role quyết định.</p>
      {([
        ['common_send_at_night', 'Cho gửi chat chung ban đêm'],
        ['team_send_in_day', 'Cho gửi chat đội ban ngày'],
        ['dead_common_send', 'Cho người chết gửi chat chung'],
        ['dead_team_send', 'Cho người chết gửi và nhận tin đội mới'],
      ] as [keyof ChatPolicy, string][]).map(([key, label]) => <label key={key} className="consent">
        <input type="checkbox" checked={policyDraft[key]} onChange={(event) =>
          setPolicyDraft((current) => current ? { ...current, [key]: event.target.checked } : current)} />
        <span>{label}</span>
      </label>)}
      <label htmlFor="chat-policy-reason">Lý do thay đổi</label>
      <input id="chat-policy-reason" maxLength={1000} value={policyReason}
        onChange={(event) => setPolicyReason(event.target.value)} />
      <button disabled={busy || !policyReason.trim() || JSON.stringify(policyDraft) === JSON.stringify(context?.policy)}>Lưu chính sách</button>
    </form>}
    {confirmationDialog}
  </section>
}
