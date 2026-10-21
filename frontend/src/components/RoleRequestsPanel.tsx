import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, jsonBody, type LinkableRoleAction, type RoleRequest, type RoleRequestContext, type RoleRequestPage, type Room } from '../api'
import { useRequestIds } from '../useRequestIds'
import { RequestGlyph } from './StoryGlyph'

function mergeRequests(previous: RoleRequest[], next: RoleRequest[]): RoleRequest[] {
  const byId = new Map(previous.map((item) => [item.id, item]))
  for (const item of next) byId.set(item.id, item)
  return [...byId.values()].sort((a, b) => a.sequence - b.sequence)
}

const phaseName = (phase: 'day' | 'night') => phase === 'day' ? 'ban ngày' : 'ban đêm'
const statusName: Record<string, string> = { pending: 'Chờ xử lý', in_review: 'Đang xét',
  accepted: 'Đã chấp nhận', rejected: 'Đã từ chối', withdrawn: 'Đã rút' }
const actionName: Record<string, string> = { 'player.kill': 'Giết', 'player.revive': 'Hồi sinh',
  'player.add_effect': 'Thêm hiệu ứng', 'player.remove_effect': 'Bỏ hiệu ứng',
  'player.change_role': 'Đổi role', 'player.change_faction': 'Đổi phe', 'player.change_team': 'Đổi đội chat' }

export default function RoleRequestsPanel({ room }: { room: Room }) {
  const [context, setContext] = useState<RoleRequestContext | null>(null)
  const [requests, setRequests] = useState<RoleRequest[]>([])
  const [before, setBefore] = useState<number | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [reviewStatus, setReviewStatus] = useState<'in_review' | 'accepted' | 'rejected'>('in_review')
  const [reply, setReply] = useState('')
  const [actions, setActions] = useState<LinkableRoleAction[]>([])
  const [linkedIds, setLinkedIds] = useState<string[]>([])
  const refreshRef = useRef<() => void>(() => {})
  const requestIdFor = useRequestIds()
  const base = `/api/rooms/${room.id}/role-requests`

  useEffect(() => {
    let active = true
    const load = () => { void api<RoleRequestContext>(`${base}/context`)
      .then((next) => { if (active) setContext(next) })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : 'Không tải được quyền gửi yêu cầu.') }) }
    load()
    const timer = window.setInterval(load, 6000)
    return () => { active = false; window.clearInterval(timer) }
  }, [base, room.version])

  useEffect(() => {
    let active = true
    let first = true
    let latestUpdate = 0
    let fetching = false
    let fetchAgain = false
    setRequests([])
    setBefore(null)
    async function refresh() {
      if (fetching) { fetchAgain = true; return }
      fetching = true
      try {
        if (first) {
          const page = await api<RoleRequestPage>(base)
          if (!active) return
          setRequests(page.requests)
          setBefore(page.next_before_sequence)
          latestUpdate = page.room_cursor
          first = false
          return
        }
        let cursor = latestUpdate
        while (true) {
          const page = await api<RoleRequestPage>(`${base}?after_updated_sequence=${cursor}&limit=50`)
          if (!active) return
          if (page.requests.length) {
            setRequests((current) => mergeRequests(current, page.requests))
            latestUpdate = page.requests.at(-1)!.updated_sequence
          }
          if (page.next_after_sequence === null) break
          cursor = page.next_after_sequence
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : 'Không tải được yêu cầu role.')
      } finally {
        fetching = false
        if (active && fetchAgain) { fetchAgain = false; void refresh() }
      }
    }
    void refresh()
    refreshRef.current = () => void refresh()
    const timer = window.setInterval(() => void refresh(), 5000)
    return () => { active = false; window.clearInterval(timer); refreshRef.current = () => {} }
  }, [base])

  async function loadOlder() {
    if (before === null || loadingOlder) return
    setLoadingOlder(true)
    try {
      const page = await api<RoleRequestPage>(`${base}?before_sequence=${before}&limit=50`)
      setRequests((current) => mergeRequests(current, page.requests))
      setBefore(page.next_before_sequence)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không tải được yêu cầu cũ.')
    } finally { setLoadingOlder(false) }
  }

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!context?.can_submit || busy || !text.trim()) return
    const payload = { text: text.trim() }
    const request = requestIdFor({ room_id: room.id, role_request: payload })
    setBusy(true)
    setError('')
    void (async () => {
      let sent = false
      try {
        await api(base, { method: 'POST', body: jsonBody({ ...payload, client_request_id: request.id }) })
        sent = true
        request.acknowledge()
        setText('')
        refreshRef.current()
      } catch (cause) {
        setError(sent ? 'Yêu cầu đã gửi, nhưng chưa tải lại được. Vui lòng mở lại tab để xem.'
          : cause instanceof Error ? cause.message : 'Không gửi được yêu cầu.')
      } finally { setBusy(false) }
    })()
  }

  function openReview(item: RoleRequest) {
    if (selectedId === item.id) { setSelectedId(null); return }
    setSelectedId(item.id)
    setReviewStatus(item.status === 'in_review' ? 'accepted' : 'in_review')
    setReply(item.reply_text ?? '')
    setLinkedIds([])
    setActions([])
    setError('')
    void api<LinkableRoleAction[]>(`${base}/${item.id}/linkable-actions`)
      .then(setActions).catch((cause) => setError(cause instanceof Error ? cause.message : 'Không tải được thao tác.'))
  }

  function submitReview(event: FormEvent<HTMLFormElement>, item: RoleRequest) {
    event.preventDefault()
    if (busy || (reviewStatus !== 'in_review' && !reply.trim())) return
    const payload = { status: reviewStatus, reply_text: reply.trim() || null,
      linked_event_ids: reviewStatus === 'accepted' ? linkedIds : [] }
    const request = requestIdFor({ room_id: room.id, role_request_id: item.id, review: payload })
    setBusy(true)
    setError('')
    void api(`${base}/${item.id}/review`, { method: 'POST',
      body: jsonBody({ ...payload, client_request_id: request.id }) })
      .then(() => { request.acknowledge(); setSelectedId(null); refreshRef.current() })
      .catch((cause) => setError(cause instanceof Error ? cause.message : 'Không xử lý được yêu cầu.'))
      .finally(() => setBusy(false))
  }

  function withdraw(item: RoleRequest) {
    if (busy || !window.confirm('Rút yêu cầu này? Quản trò sẽ thấy trạng thái đã rút.')) return
    const request = requestIdFor({ room_id: room.id, role_request_id: item.id, withdraw: true })
    setBusy(true)
    setError('')
    void api(`${base}/${item.id}/withdraw`, { method: 'POST',
      body: jsonBody({ client_request_id: request.id }) })
      .then(() => { request.acknowledge(); refreshRef.current() })
      .catch((cause) => setError(cause instanceof Error ? cause.message : 'Không rút được yêu cầu.'))
      .finally(() => setBusy(false))
  }

  return <section className="role-requests-panel" aria-labelledby="role-requests-title">
    <span className="eyebrow">GỬI QUẢN TRÒ</span>
    <h2 id="role-requests-title">Yêu cầu dùng role</h2>
    <p>Nội dung yêu cầu có thể là một con số, nhiều người hoặc không có mục tiêu. Điều kiện dùng role nằm trong mô tả role; quản trò tự xét và quyết định.</p>
    <p className="request-privacy">Trong trận chỉ người gửi và quản trò thấy yêu cầu. Sau trận, yêu cầu sẽ được công bố trong nhật ký cho người từng tham gia.</p>
    {!room.is_host && context && <div className="request-permission">
      <strong>{context.role_name ?? 'Chưa có role'}</strong>
      <span>{context.allowed_phases.length
        ? `Pha được gửi: ${context.allowed_phases.map(phaseName).join(', ')}`
        : 'Role không có pha chủ động gửi yêu cầu'}{context.request_when_dead ? ' · được gửi khi chết' : ''}</span>
    </div>}
    {!room.is_host && context?.can_submit && <form className="role-request-form" onSubmit={send}>
      <label htmlFor="role-request-text">Nội dung gửi quản trò</label>
      <textarea id="role-request-text" maxLength={2000} value={text}
        onChange={(event) => setText(event.target.value)} />
      <small>{text.length}/2000 ký tự</small>
      <button disabled={busy || !text.trim()}>{busy ? 'Đang gửi…' : 'Gửi yêu cầu'}</button>
    </form>}
    {!room.is_host && context && !context.can_submit && <p className="request-blocked" role="status">{context.reason}</p>}
    <h3>{room.is_host ? 'Yêu cầu đã nhận' : 'Yêu cầu của bạn'}</h3>
    {before !== null && <button type="button" className="button-secondary request-older"
      disabled={loadingOlder} onClick={() => void loadOlder()}>{loadingOlder ? 'Đang tải…' : 'Xem yêu cầu cũ hơn'}</button>}
    {error && <p className="form-message" role="alert">{error}</p>}
    {requests.length ? <ol className="role-request-list">
      {[...requests].sort((a, b) => room.is_host
        ? Number(['pending', 'in_review'].includes(b.status)) - Number(['pending', 'in_review'].includes(a.status)) || b.updated_sequence - a.updated_sequence
        : b.sequence - a.sequence).map((item) => <li key={item.id}>
        <div className="role-request-heading"><strong>{room.is_host ? item.sender_name : item.role_name}</strong>
          <span>{phaseName(item.phase_type)} {item.round_number} · <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('vi-VN')}</time></span></div>
        {room.is_host && <small>Role khi gửi: {item.role_name}</small>}
        <p>{item.text}</p><span className="request-status"><RequestGlyph status={item.status} size={20} />{statusName[item.status] ?? item.status}</span>
        {item.reply_text && <p className="request-reply"><strong>Phản hồi quản trò:</strong> {item.reply_text}</p>}
        {room.is_host && item.linked_event_ids.length > 0 && <small>Đã liên kết {item.linked_event_ids.length} thao tác.</small>}
        {room.status === 'playing' && ['pending', 'in_review'].includes(item.status) && (room.is_host
          ? <button type="button" className="button-secondary" onClick={() => openReview(item)}>
            {selectedId === item.id ? 'Đóng' : 'Xử lý yêu cầu'}</button>
          : <button type="button" className="button-secondary" disabled={busy} onClick={() => withdraw(item)}>Rút yêu cầu</button>)}
        {room.is_host && selectedId === item.id && <form className="request-review-form" onSubmit={(event) => submitReview(event, item)}>
          <label>Trạng thái
            <select value={reviewStatus} onChange={(event) => { setReviewStatus(event.target.value as typeof reviewStatus); setLinkedIds([]) }}>
              {item.status === 'pending' && <option value="in_review">Đang xét</option>}
              <option value="accepted">Chấp nhận</option><option value="rejected">Từ chối</option>
            </select></label>
          <label>Phản hồi cho người gửi
            <textarea maxLength={2000} value={reply} onChange={(event) => setReply(event.target.value)}
              placeholder="Ghi kết quả hoặc thông tin đang xét; bắt buộc khi chốt." /></label>
          {reviewStatus === 'accepted' && <fieldset><legend>Thao tác đã thực hiện (nếu có)</legend>
            <p>Thực hiện trong Bàn quản trò trước, rồi chọn thao tác để ghi liên kết. Yêu cầu chỉ cung cấp thông tin có thể chấp nhận mà không cần thao tác.</p>
            {actions.length ? actions.map((action) => <label key={action.event_id} className="request-action-option">
              <input type="checkbox" checked={linkedIds.includes(action.event_id)} disabled={busy || (!linkedIds.includes(action.event_id) && linkedIds.length >= 10)}
                onChange={(event) => setLinkedIds((current) => event.target.checked
                  ? [...current, action.event_id] : current.filter((id) => id !== action.event_id))} />
              {actionName[action.kind] ?? action.kind} · {action.target_name} · #{action.sequence}
            </label>) : <small>Chưa có thao tác nào sau yêu cầu này.</small>}
          </fieldset>}
          <button disabled={busy || (reviewStatus !== 'in_review' && !reply.trim())}>{busy ? 'Đang lưu…' : 'Lưu phản hồi'}</button>
        </form>}
      </li>)}
    </ol> : <p>Chưa có yêu cầu nào.</p>}
  </section>
}
