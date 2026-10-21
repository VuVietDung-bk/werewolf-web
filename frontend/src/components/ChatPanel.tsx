import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, jsonBody, type ChatChannel, type ChatContext, type ChatMessage, type ChatPage, type ChatPolicy, type Room } from '../api'
import { useRequestIds } from '../useRequestIds'
import { useConfirmation } from '../useConfirmation'
import ChatGlyph from './ChatGlyph'

const channelNames: Record<ChatChannel, string> = {
  common: 'Chat chung', team: 'Chat đội', host: 'Chat quản trò',
}
const channelTabNames: Record<ChatChannel, string> = {
  common: 'Chung', team: 'Đội', host: 'Quản trò',
}

function mergeMessages(previous: ChatMessage[], next: ChatMessage[]): ChatMessage[] {
  const byId = new Map(previous.map((message) => [message.id, message]))
  for (const message of next) {
    if (message.deleted && !message.history) byId.delete(message.id)
    else byId.set(message.id, message)
  }
  return [...byId.values()].sort((a, b) => a.sequence - b.sequence)
}

type ChatActivity = ChatContext['activity'][number]
const activityKey = (item: ChatActivity) => JSON.stringify([
  item.channel, item.team_label, item.participant_user_id,
])

export default function ChatPanel({ room, userId, onRoomChanged }: { room: Room; userId: string; onRoomChanged: () => Promise<void> }) {
  const [channel, setChannel] = useState<ChatChannel>('common')
  const [context, setContext] = useState<ChatContext | null>(null)
  const [policyDraft, setPolicyDraft] = useState<ChatPolicy | null>(null)
  const [policyReason, setPolicyReason] = useState('')
  const [mutePlayerId, setMutePlayerId] = useState(room.members[0]?.user_id ?? '')
  const [muteReason, setMuteReason] = useState('')
  const [teamLabel, setTeamLabel] = useState('')
  const [participantId, setParticipantId] = useState(room.members[0]?.user_id ?? '')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loadedThrough, setLoadedThrough] = useState<{ scope: string; sequence: number } | null>(null)
  const seenKey = `ma-soi-chat-seen:${room.id}:${userId}`
  const [seen, setSeen] = useState<Record<string, number>>(() => {
    try { return JSON.parse(window.sessionStorage.getItem(seenKey) || '{}') as Record<string, number> }
    catch { return {} }
  })
  const [before, setBefore] = useState<number | null>(null)
  const [text, setText] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingText, setEditingText] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [error, setError] = useState('')
  const [connectionState, setConnectionState] = useState<'connecting' | 'live' | 'fallback'>('connecting')
  const refreshRef = useRef<() => void>(() => {})
  const refreshContextRef = useRef<() => void>(() => {})
  const onRoomChangedRef = useRef(onRoomChanged)
  onRoomChangedRef.current = onRoomChanged
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
    refreshContextRef.current = () => void refreshContext()
    const timer = window.setInterval(() => void refreshContext(), 6000)
    return () => { active = false; window.clearInterval(timer); refreshContextRef.current = () => {} }
  }, [base])

  useEffect(() => {
    if (context && room.status !== 'finished' && channel === 'team' && context.team_labels.length === 0) setChannel('common')
  }, [channel, context, room.status])

  const selectedParticipant = room.is_host && room.status !== 'finished' ? participantId : undefined
  const selectedTeam = room.is_host && room.status !== 'finished' && channel === 'team' ? teamLabel : undefined
  const canRead = room.status === 'finished' || channel !== 'host' || !room.is_host || Boolean(selectedParticipant)
  const viewScope = JSON.stringify([
    channel, channel === 'team' ? selectedTeam ?? null : null,
    channel === 'host' ? selectedParticipant ?? null : null,
  ])

  useEffect(() => {
    if (!context || !loadedThrough || loadedThrough.scope !== viewScope) return
    let next: Record<string, number> | null = null
    for (const item of context.activity) {
      if (item.channel !== channel || item.sequence > loadedThrough.sequence) continue
      if (room.is_host && channel === 'team' && item.team_label !== selectedTeam) continue
      if (room.is_host && channel === 'host' && item.participant_user_id !== selectedParticipant) continue
      const key = activityKey(item)
      if (item.sequence > (seen[key] ?? 0)) {
        if (!next) next = { ...seen }
        next[key] = item.sequence
      }
    }
    if (next) {
      setSeen(next)
      try { window.sessionStorage.setItem(seenKey, JSON.stringify(next)) } catch { /* keep in memory */ }
    }
  }, [channel, context, loadedThrough, room.is_host, seen, seenKey, selectedParticipant, selectedTeam, viewScope])

  const unread = (['common', 'team', 'host'] as ChatChannel[]).reduce((result, item) => {
    result[item] = Boolean(context?.activity.some((entry) =>
      entry.channel === item && entry.sequence > (seen[activityKey(entry)] ?? 0)))
    return result
  }, {} as Record<ChatChannel, boolean>)

  function query(beforeSequence?: number, afterSequence?: number, afterUpdatedSequence?: number): string {
    const params = new URLSearchParams({ channel })
    if (selectedTeam) params.set('team_label', selectedTeam)
    if (channel === 'host' && selectedParticipant) params.set('participant_user_id', selectedParticipant)
    if (beforeSequence) params.set('before_sequence', String(beforeSequence))
    if (afterSequence !== undefined) params.set('after_sequence', String(afterSequence))
    if (afterUpdatedSequence !== undefined) params.set('after_updated_sequence', String(afterUpdatedSequence))
    return `${base}/messages?${params}`
  }

  useEffect(() => {
    let active = true
    let first = true
    let latestSequence = 0
    let latestUpdate = 0
    let fetching = false
    let fetchAgain = false
    setMessages([])
    setBefore(null)
    setLoadedThrough(null)
    setError('')
    if (!canRead) return () => { active = false }
    async function refresh() {
      if (fetching) { fetchAgain = true; return }
      fetching = true
      try {
        if (first) {
          const page = await api<ChatPage>(query())
          if (!active) return
          setMessages(page.messages)
          setBefore(page.next_before_sequence)
          latestSequence = page.messages.at(-1)?.sequence ?? 0
          latestUpdate = page.room_cursor
          setLoadedThrough({ scope: viewScope, sequence: latestSequence })
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
        let updateCursor = latestUpdate
        while (true) {
          const page: ChatPage = await api<ChatPage>(query(undefined, undefined, updateCursor))
          if (!active) return
          if (page.messages.length) {
            setMessages((current) => {
              const loaded = new Set(current.map((item) => item.id))
              return mergeMessages(current, page.messages.filter((item) => loaded.has(item.id)))
            })
          }
          if (page.next_after_updated_sequence === null) {
            latestUpdate = page.room_cursor
            break
          }
          updateCursor = page.next_after_updated_sequence
        }
        setLoadedThrough({ scope: viewScope, sequence: latestSequence })
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : 'Không tải được chat.')
      } finally {
        fetching = false
        if (active && fetchAgain) { fetchAgain = false; void refresh() }
      }
    }
    void refresh()
    refreshRef.current = () => void refresh()
    const timer = window.setInterval(() => void refresh(), 5000)
    return () => { active = false; window.clearInterval(timer); refreshRef.current = () => {} }
  }, [base, channel, selectedParticipant, selectedTeam, canRead, viewScope, room.status])

  useEffect(() => {
    let active = true
    let chatTimer: number | null = null
    let contextTimer: number | null = null
    let reconnectTimer: number | null = null
    let socket: WebSocket | null = null
    let attempts = 0
    const cursorKey = `ma-soi-live-cursor:${room.id}`
    let cursor = 0
    try { cursor = Number(window.sessionStorage.getItem(cursorKey)) || 0 } catch { /* storage can be disabled */ }
    const seen = new Set<string>()
    const saveCursor = (sequence: number) => {
      cursor = sequence
      try { window.sessionStorage.setItem(cursorKey, String(cursor)) } catch { /* keep in memory */ }
    }
    const refreshAll = () => {
      refreshRef.current()
      refreshContextRef.current()
      void onRoomChangedRef.current().catch(() => {})
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const reconnect = () => {
      if (!active) return
      setConnectionState('fallback')
      const delay = Math.min(10000, 500 * 2 ** Math.min(attempts++, 5))
      reconnectTimer = window.setTimeout(connect, delay)
    }
    function connect() {
      if (!active) return
      setConnectionState('connecting')
      const suffix = cursor > 0 ? `?after_sequence=${cursor}` : ''
      try { socket = new WebSocket(`${protocol}//${window.location.host}/api/rooms/${room.id}/live${suffix}`) }
      catch { reconnect(); return }
      socket.onmessage = (message) => {
        let notice: { type?: string; cursor?: number; event_id?: string; sequence?: number; kind?: string }
        try { notice = JSON.parse(message.data) as typeof notice } catch { return }
        if (!active) return
        if (notice.type === 'ready' && Number.isSafeInteger(notice.cursor)) {
          saveCursor(notice.cursor!)
          attempts = 0
          setConnectionState('live')
          refreshAll()
          return
        }
        if (notice.type !== 'game.event' || !notice.event_id || !notice.kind || !Number.isSafeInteger(notice.sequence)) return
        saveCursor(Math.max(cursor, notice.sequence!))
        if (seen.has(notice.event_id)) return
        seen.add(notice.event_id)
        if (seen.size > 1000) seen.delete(seen.values().next().value!)
        if (notice.kind === 'chat.sent' || notice.kind === 'chat.revised') {
          if (chatTimer !== null) window.clearTimeout(chatTimer)
          chatTimer = window.setTimeout(() => { refreshRef.current(); chatTimer = null }, 80)
          if (contextTimer !== null) window.clearTimeout(contextTimer)
          contextTimer = window.setTimeout(() => { refreshContextRef.current(); contextTimer = null }, 80)
        } else {
          if (contextTimer !== null) window.clearTimeout(contextTimer)
          contextTimer = window.setTimeout(() => { refreshContextRef.current(); contextTimer = null }, 80)
          if (notice.kind.startsWith('game.') || notice.kind.startsWith('room.') || notice.kind.startsWith('member.')) {
            void onRoomChangedRef.current().catch(() => {})
          }
        }
      }
      socket.onclose = (event) => {
        if (!active) return
        if ([4400, 4401, 4403, 4404].includes(event.code)) { setConnectionState('fallback'); return }
        reconnect()
      }
      socket.onerror = () => { if (active) setConnectionState('fallback') }
    }
    connect()
    return () => {
      active = false
      if (chatTimer !== null) window.clearTimeout(chatTimer)
      if (contextTimer !== null) window.clearTimeout(contextTimer)
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [room.id])

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

  async function reviseMessage(message: ChatMessage, action: 'edit' | 'delete') {
    const nextText = action === 'edit' ? editingText.trim() : null
    if (busy || (action === 'edit' && (!nextText || nextText === message.text))) return
    if (action === 'delete') {
      const confirmed = await ask({ title: 'Xóa tin nhắn',
        phase: `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}`,
        subject: channelNames[message.channel],
        audience: message.channel === 'common' ? 'Toàn phòng' : message.channel === 'team' ? `Đội ${message.team_label}` : 'Người chơi nhận tin',
        publicText: 'Tin sẽ biến mất khỏi chat hiện tại. Nội dung gốc và thao tác xóa vẫn hiện trong lịch sử sau trận.',
        confirmLabel: 'Xóa tin' })
      if (!confirmed) return
    }
    const payload = { action, text: nextText }
    const request = requestIdFor({ room_id: room.id, source_event_id: message.event_id, ...payload })
    setBusy(true)
    setError('')
    try {
      await api(`/api/rooms/${room.id}/game/content/${message.event_id}/revisions`, {
        method: 'POST', body: jsonBody({ ...payload, client_request_id: request.id }),
      })
      request.acknowledge()
      setEditingId(null)
      const page = await api<ChatPage>(query())
      setMessages((current) => {
        const withoutDeleted = current.filter((item) => item.id !== message.id || action !== 'delete')
        return mergeMessages(withoutDeleted, page.messages)
      })
      refreshRef.current()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không sửa được tin nhắn.')
    } finally { setBusy(false) }
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

  async function changeMute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!room.is_host || !mutePlayerId || !muteReason.trim() || busy) return
    const member = room.members.find((item) => item.user_id === mutePlayerId)
    const muted = !context?.muted_players.some((item) => item.user_id === mutePlayerId)
    const confirmed = await ask({ title: muted ? 'Chặn gửi chat' : 'Mở lại chat',
      phase: room.status === 'playing' ? `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}` : 'Trước trận',
      subject: member?.display_name ?? 'Người chơi', audience: 'Quản trò và người chơi này',
      publicText: muted ? 'Người chơi tạm thời chỉ đọc chat.' : 'Người chơi có thể gửi chat theo quyền role và pha.',
      note: `Lý do: ${muteReason.trim()}. Thao tác được lưu vào sự kiện.`,
      confirmLabel: muted ? 'Chặn gửi chat' : 'Mở lại chat' })
    if (!confirmed) return
    const payload = { muted, reason: muteReason.trim() }
    const request = requestIdFor({ room_id: room.id, mute_player_id: mutePlayerId, ...payload })
    setBusy(true)
    setError('')
    let saved = false
    try {
      await api(`${base}/players/${mutePlayerId}/mute`, { method: 'PATCH',
        body: jsonBody({ ...payload, client_request_id: request.id }) })
      saved = true
      request.acknowledge()
      setContext(await api<ChatContext>(`${base}/context`))
      setMuteReason('')
    } catch (cause) {
      setError(saved ? 'Đã đổi quyền chat, nhưng chưa tải lại được. Vui lòng mở lại tab Chat.'
        : cause instanceof Error ? cause.message : 'Không đổi được quyền chat.')
    } finally { setBusy(false) }
  }

  const canSend = channel === 'common' ? context?.can_send_common
    : channel === 'team' ? context?.can_send_team && (!room.is_host || context.writable_team_labels.includes(teamLabel))
      : context?.can_send_host && (!room.is_host || Boolean(participantId))

  return <section className="chat-panel" aria-labelledby="chat-title">
    <span className="eyebrow">CHUYỆN TRONG LÀNG</span><h2 id="chat-title">Chat</h2>
    <small className="chat-connection" role="status">{connectionState === 'live' ? 'Đang nhận tin trực tiếp' : connectionState === 'connecting' ? 'Đang kết nối chat…' : 'Đang tự cập nhật tin mới'}</small>
    <p>{room.status === 'finished'
      ? 'Sau trận, mọi người từng tham gia xem được cả ba kênh và từng bản sửa/xóa trong lịch sử mỗi tin.'
      : 'Chat đội và chat với quản trò chỉ hiện cho người có quyền trong trận. Sau trận, toàn bộ chat được công bố cho người từng tham gia.'}</p>
    {context && <p className="chat-policy-summary">Quyền hiện tại: chat chung ban đêm {context.policy.common_send_at_night ? 'được gửi' : 'chỉ đọc'}; chat đội ban ngày {context.policy.team_send_in_day ? 'được gửi' : 'chỉ đọc'}; người chết {context.policy.dead_common_send ? 'được gửi chat chung' : 'chỉ đọc chat chung'} và {context.policy.dead_team_send ? 'được tham gia chat đội' : 'chỉ đọc tin đội cũ'}.</p>}
    {context?.is_muted && <p className="chat-mute-notice" role="status">Quản trò đã tạm chặn quyền gửi chat của bạn. Lý do: {context.mute_reason}</p>}
    <div className="chat-channel-tabs" role="group" aria-label="Kênh chat">
      {(['common', ...(room.status === 'finished' || context?.team_labels.length ? ['team'] : []), 'host'] as ChatChannel[]).map((item) => <button key={item} type="button"
        className={`${channel === item ? 'active ' : ''}${unread[item] ? 'has-unread' : ''}`} aria-pressed={channel === item}
        onClick={() => { setChannel(item); setText('') }}>
        <ChatGlyph channel={item} /><span className="chat-tab-label">{channelTabNames[item]}</span>
        {unread[item] && <span className="chat-unread" aria-label="Có tin mới">Mới</span>}
      </button>)}
    </div>
    {channel === 'team' && room.is_host && room.status !== 'finished' && <label className="chat-picker">Đội chat
      <select value={teamLabel} onChange={(event) => setTeamLabel(event.target.value)}>
        {context?.team_labels.length ? context.team_labels.map((label) => <option key={label} value={label}>{label}{context.writable_team_labels.includes(label) ? '' : ' · lịch sử'}</option>)
          : <option value="">Chưa có đội chat</option>}
      </select></label>}
    {channel === 'host' && room.is_host && room.status !== 'finished' && <label className="chat-picker">Người chơi
      <select value={participantId} onChange={(event) => setParticipantId(event.target.value)}>
        {room.members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
      </select></label>}
    {channel === 'team' && !room.is_host && room.status !== 'finished' && <p className="chat-scope">Đội hiện tại: {context?.current_team_label || 'Không có'} · Tin của các đội bạn từng tham gia vẫn đọc được.</p>}
    {channel === 'host' && !room.is_host && room.status !== 'finished' && <p className="chat-scope">Chỉ bạn và quản trò thấy cuộc trò chuyện này trong trận.</p>}
    {before !== null && <button type="button" className="button-secondary chat-older" disabled={loadingOlder}
      onClick={() => void loadOlder()}>{loadingOlder ? 'Đang tải…' : 'Xem tin cũ hơn'}</button>}
    <ol className="chat-message-list" aria-live="polite">
      {messages.map((message) => <li key={message.id}>
        <div><strong>{message.sender_user_id === room.host_id ? 'Quản trò' : message.sender_name}</strong>
          {channel === 'team' && message.team_label && <span> · {message.team_label}</span>}
          {channel === 'host' && room.status === 'finished' && <span> · {room.members.find((member) => member.user_id === message.participant_user_id)?.display_name ?? 'Người chơi'}</span>}
          <time dateTime={message.created_at}>{new Date(message.created_at).toLocaleString('vi-VN')}</time></div>
        <p>{message.deleted ? 'Tin nhắn đã xóa' : message.text}{message.edited_at && !message.deleted ? <small> · đã sửa</small> : null}</p>
        {room.is_host && room.status === 'playing' && message.sender_user_id === userId && !message.deleted && <div className="chat-message-actions">
          <button type="button" className="button-secondary" disabled={busy} onClick={() => { setEditingId(message.id); setEditingText(message.text) }}>Sửa</button>
          <button type="button" className="button-secondary" disabled={busy} onClick={() => void reviseMessage(message, 'delete')}>Xóa</button>
        </div>}
        {editingId === message.id && <form className="chat-edit" onSubmit={(event) => { event.preventDefault(); void reviseMessage(message, 'edit') }}>
          <label>Chỉnh tin nhắn<textarea maxLength={2000} value={editingText} onChange={(event) => setEditingText(event.target.value)} /></label>
          <div><button disabled={busy || !editingText.trim() || editingText.trim() === message.text}>Lưu</button>
            <button type="button" className="button-secondary" onClick={() => setEditingId(null)}>Hủy</button></div>
        </form>}
        {message.history && <details className="chat-history"><summary>Lịch sử đầy đủ ({message.history.length} phiên bản)</summary>
          <ol>{message.history.map((entry) => <li key={entry.sequence}>
            <time dateTime={entry.created_at}>{new Date(entry.created_at).toLocaleString('vi-VN')}</time>
            <strong>{entry.action === 'send' ? 'Gửi' : entry.action === 'edit' ? 'Sửa' : 'Xóa'}</strong>
            {entry.text && <p>{entry.text}</p>}
          </li>)}</ol>
        </details>}
      </li>)}
    </ol>
    {!messages.length && <p className="chat-empty">Chưa có tin nhắn ở kênh này.</p>}
    {error && <p className="form-message" role="alert">{error}</p>}
    {canSend ? <form className="chat-send" onSubmit={send}>
      <label htmlFor="chat-text">Gửi vào {channelNames[channel].toLowerCase()}</label>
      <textarea id="chat-text" maxLength={2000} value={text} onChange={(event) => setText(event.target.value)} />
      <button disabled={busy || !text.trim()}>{busy ? 'Đang gửi…' : 'Gửi tin'}</button>
    </form> : <p className="chat-scope">Hiện tại bạn chỉ có thể đọc kênh này.</p>}
    {room.is_host && room.status !== 'finished' && <details className="chat-settings">
      <summary>Cài đặt chat và chặn người chơi</summary>
      {policyDraft && <form className="chat-policy" onSubmit={(event) => void savePolicy(event)}>
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
      <form className="chat-policy" onSubmit={(event) => void changeMute(event)}>
      <h3>Quản lý quyền gửi chat từng người</h3>
      <p>Người bị chặn vẫn đọc được các kênh họ có quyền xem. Chặn và mở lại đều cần lý do.</p>
      <label htmlFor="chat-mute-player">Người chơi</label>
      <select id="chat-mute-player" value={mutePlayerId} onChange={(event) => setMutePlayerId(event.target.value)}>
        {room.members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
      </select>
      {context?.muted_players.find((item) => item.user_id === mutePlayerId) &&
        <p className="chat-mute-notice">Đang bị chặn: {context.muted_players.find((item) => item.user_id === mutePlayerId)?.reason}</p>}
      <label htmlFor="chat-mute-reason">Lý do</label>
      <input id="chat-mute-reason" maxLength={1000} value={muteReason}
        onChange={(event) => setMuteReason(event.target.value)} />
      <button disabled={busy || !mutePlayerId || !muteReason.trim()}>
        {context?.muted_players.some((item) => item.user_id === mutePlayerId) ? 'Mở lại chat' : 'Chặn gửi chat'}
      </button>
      </form>
    </details>}
    {confirmationDialog}
  </section>
}
