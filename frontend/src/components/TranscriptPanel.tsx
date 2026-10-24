import { useEffect, useState, type FormEvent } from 'react'
import { api, type Room, type TranscriptEvent, type TranscriptInfo, type TranscriptPage } from '../api'
import { NoticeGlyph } from './StoryGlyph'

const categories = [
  ['all', 'Tất cả'], ['roles', 'Chọn và phát role'], ['role', 'Role và yêu cầu'],
  ['chat', 'Chat'], ['vote', 'Vote'], ['player', 'Người chơi và ghi chú'],
  ['event', 'Thông báo và đính chính'], ['content', 'Sửa và xóa'],
  ['game', 'Diễn biến trận'], ['member', 'Thành viên'], ['room', 'Phòng'],
] as const
type Category = typeof categories[number][0]

const labels: Record<string, string> = {
  'roles.draft_started': 'Bắt đầu chọn role', 'roles.draft_offered': 'Đưa ra các role',
  'roles.draft_chosen': 'Chốt role', 'roles.draft_completed': 'Hoàn tất chọn role',
  'roles.distributed': 'Phát role ngẫu nhiên', 'role.request_submitted': 'Gửi yêu cầu role',
  'role.request_reviewed': 'Phản hồi yêu cầu role', 'role.request_withdrawn': 'Rút yêu cầu role',
  'chat.sent': 'Tin nhắn', 'chat.revised': 'Sửa hoặc xóa tin nhắn',
  'vote.opened': 'Mở vote', 'vote.ballot_submitted': 'Bỏ hoặc đổi phiếu',
  'vote.closed': 'Đóng vote', 'vote.decided': 'Quản trò chốt vote',
  'vote.publication_decided': 'Chọn cách công bố vote', 'vote.result_announced': 'Công bố vote',
  'player.kill': 'Đánh dấu chết', 'player.revive': 'Hồi sinh',
  'player.note': 'Ghi chú quản trò', 'player.add_effect': 'Thêm hiệu ứng',
  'player.remove_effect': 'Gỡ hiệu ứng', 'player.change_role': 'Đổi role',
  'player.change_faction': 'Đổi phe', 'player.change_team': 'Đổi đội chat',
  'player.publication_disposition_decided': 'Giữ hoặc bỏ thông báo',
  'player.announcement_decided': 'Chốt thông báo trạng thái',
  'player.death_announced': 'Công bố người chết',
  'player.revival_announced': 'Công bố hồi sinh',
  'player.private_message_sent': 'Tin riêng quản trò',
  'event.custom_announcement': 'Thông báo công khai',
  'event.correction_decided': 'Đính chính quyết định',
  'event.public_correction': 'Công bố đính chính',
  'content.revision_decided': 'Sửa hoặc xóa nội dung',
  'content.note_revision_decided': 'Sửa hoặc xóa ghi chú',
  'game.started': 'Bắt đầu trận', 'game.phase_advanced': 'Chuyển pha',
  'game.finished': 'Kết thúc trận', 'game.paused': 'Tạm dừng trận',
  'game.resumed': 'Tiếp tục trận',
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function named(value: unknown, map: Record<string, string>): string {
  if (typeof value !== 'string') return ''
  return map[value] ?? value
}

function summary(item: TranscriptEvent, info: TranscriptInfo): string {
  const payload = item.payload
  const request = object(payload.request)
  if (item.kind === 'roles.draft_started') {
    const order = Array.isArray(payload.turn_order) ? payload.turn_order.map((id) => named(id, info.members)) : []
    const first = Array.isArray(payload.first_offers) ? payload.first_offers.map((id) => named(id, info.roles)) : []
    return `Thứ tự chọn: ${order.join(' → ') || 'xem dữ liệu gốc'}${first.length ? ` · Lượt đầu: ${first.join(' / ')}` : ''}`
  }
  if (item.kind === 'roles.draft_offered') {
    const offers = Array.isArray(payload.offers) ? payload.offers.map((id) => named(id, info.roles)) : []
    return `${named(payload.user_id, info.members)} · ${offers.join(' / ')}`
  }
  if (item.kind === 'roles.draft_chosen') {
    const mode: Record<string, string> = { self: 'tự chọn', self_random: 'tự lấy ngẫu nhiên', host_random: 'quản trò chọn ngẫu nhiên' }
    const offered = Array.isArray(payload.offers) ? payload.offers.map((id) => named(id, info.roles)).join(' / ') : ''
    return `${named(payload.user_id, info.members)} → ${named(payload.chosen_role_id, info.roles)} · ${mode[String(payload.choice_mode)] ?? String(payload.choice_mode)}${offered ? ` · Đã mời: ${offered}` : ''}`
  }
  if (item.kind === 'roles.distributed') {
    const assignments = Array.isArray(payload.assignments) ? payload.assignments.map((value) => {
      const assignment = object(value)
      return `${named(assignment.user_id, info.members)}: ${named(assignment.role_id, info.roles)}`
    }) : []
    return `Phát ngẫu nhiên · ${assignments.join(' · ')}`
  }
  if (item.kind === 'chat.sent') {
    const channel: Record<string, string> = { common: 'Chat chung', team: 'Chat đội', host: 'Chat quản trò' }
    const recipient = payload.channel === 'host' ? ` với ${named(payload.participant_user_id, info.members)}` : ''
    return `${channel[String(payload.channel)] ?? String(payload.channel)}${recipient}: ${String(request.text ?? '')}`
  }
  if (item.kind === 'vote.ballot_submitted') {
    const choice = request.choice === 'target' ? named(request.target_user_id, info.members)
      : request.choice === 'skip' ? 'bỏ qua' : 'rút phiếu'
    return `Phiếu của ${item.actor_name ?? 'người chơi'}: ${choice}`
  }
  if (item.kind === 'vote.decided') {
    const tally = Array.isArray(payload.tally) ? payload.tally.map((value) => {
      const counted = object(value)
      return `${named(counted.target_user_id, info.members)} ${counted.count ?? 0}`
    }) : []
    return `${String(request.decision_text ?? '')} · Phiếu: ${tally.join(', ') || 'không có'} · Bỏ qua ${payload.skip_count ?? 0} · Chưa gửi ${payload.not_submitted_count ?? 0}`
  }
  if (item.kind === 'player.publication_disposition_decided') {
    return `${item.subject_name ?? 'Người chơi'}: ${payload.new_disposition === 'held' ? 'giữ thông báo' : 'bỏ thông báo'}`
  }
  if (item.kind === 'game.phase_advanced' || item.kind === 'game.started') {
    return `${item.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${item.round_number ?? 0}`
  }
  const text = request.text ?? payload.text ?? payload.new_text ?? request.decision_text
    ?? request.reply_text ?? payload.reply_text ?? request.corrected_text
  if (typeof text === 'string' && text) return text
  if (item.subject_name) return item.subject_name
  if (item.kind === 'game.finished') return `${payload.result_label ?? info.result_label ?? ''}`
  return ''
}

function glyphKind(item: TranscriptEvent): Parameters<typeof NoticeGlyph>[0]['kind'] {
  if (item.kind.includes('correction') || item.kind.startsWith('content.') || item.kind === 'chat.revised') return 'correction'
  if (item.kind.startsWith('vote.')) return 'vote'
  if (item.kind.includes('death') || item.kind === 'player.kill') return 'death'
  if (item.kind.includes('revive') || item.kind.includes('revival')) return 'revival'
  if (item.kind.startsWith('role')) return 'role'
  if (item.kind.includes('disposition')) return 'sealed'
  return 'general'
}

function phase(item: TranscriptEvent): string {
  if (item.phase_type === 'day') return `Ngày ${item.round_number}`
  if (item.phase_type === 'night') return `Đêm ${item.round_number}`
  return 'Trước trận'
}

export default function TranscriptPanel({ room }: { room: Room }) {
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<Category>('all')
  const [data, setData] = useState<TranscriptPage | null>(null)
  const [events, setEvents] = useState<TranscriptEvent[]>([])
  const [cursor, setCursor] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const params = new URLSearchParams({ category, q: query })
  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    setData(null)
    setEvents([])
    void api<TranscriptPage>(`/api/rooms/${room.id}/transcript?${params.toString()}&limit=50`)
      .then((page) => {
        if (!alive) return
        setData(page)
        setEvents(page.events)
        setCursor(page.next_after_sequence)
      })
      .catch((reason: unknown) => { if (alive) setError(reason instanceof Error ? reason.message : 'Không tải được nhật ký.') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [room.id, room.version, category, query])

  async function more() {
    if (cursor === null || loading) return
    setLoading(true)
    try {
      const page = await api<TranscriptPage>(`/api/rooms/${room.id}/transcript?${params.toString()}&limit=50&after_sequence=${cursor}`)
      setEvents((current) => [...current, ...page.events])
      setCursor(page.next_after_sequence)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Không tải được nhật ký.') }
    finally { setLoading(false) }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setQuery(search.trim())
  }

  function findEvent(id: string) {
    setSearch(id)
    setCategory('all')
    setQuery(id)
  }

  const exportParams = new URLSearchParams({ category, q: query })
  const endedAt = data?.info.ended_at ?? room.ended_at
  const deleteAt = endedAt ? new Date(new Date(endedAt).getTime() + 90 * 24 * 60 * 60 * 1000) : null
  return <section className="transcript-screen" aria-labelledby="transcript-title">
    <header className="transcript-header phase-night">
      <span className="eyebrow">SAU TRẬN · HỒ SƠ LÀNG</span>
      <h2 id="transcript-title">Nhật ký toàn bộ ván</h2>
      <p>{data?.info.result_label ?? room.result_label}{data?.info.result_reason ? ` · ${data.info.result_reason}` : ''}</p>
    </header>
    <p className="transcript-limitation">{data?.info.limitation ?? 'Nhật ký ghi nhận thao tác trên web; không tự chứng minh quản trò xét luật đúng hoặc ghi nhận hành động ngoài web.'}</p>
    {deleteAt && <p className="transcript-limitation">Phòng và nhật ký trên web đủ hạn xóa từ {deleteAt.toLocaleString('vi-VN')} (90 ngày sau khi kết thúc ván). Bản sao DB được giữ tối đa 7 ngày từ lúc sao lưu. Hãy tải JSON hoặc CSV trước thời điểm này.</p>}
    <form className="transcript-filters" onSubmit={submit}>
      <label>Loại sự kiện
        <select value={category} onChange={(event) => setCategory(event.target.value as Category)}>
          {categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>Tìm trong nhật ký
        <input value={search} maxLength={120} onChange={(event) => setSearch(event.target.value)} placeholder="Tên, nội dung, mã sự kiện…" />
      </label>
      <button type="submit">Tìm</button>
    </form>
    <div className="transcript-tools">
      <span>{data ? `${data.total} sự kiện · đang xem ${events.length}` : 'Đang tải nhật ký…'}</span>
      {data && <div className="transcript-downloads">
        <a href={`/api/rooms/${room.id}/transcript/export?${exportParams.toString()}&format=json`} download>Tải JSON</a>
        <a href={`/api/rooms/${room.id}/transcript/export?${exportParams.toString()}&format=csv`} download>Tải CSV</a>
      </div>}
    </div>
    {error && <p role="alert" className="form-message">{error}</p>}
    {data && events.length === 0 && <p>Không tìm thấy sự kiện phù hợp.</p>}
    {data && <ol className="transcript-list">
      {events.map((item) => <li key={item.id} id={`event-${item.id}`} className={`transcript-item ${item.phase_type === 'day' ? 'is-day' : item.phase_type === 'night' ? 'is-night' : ''}`}>
        <div className="transcript-icon"><NoticeGlyph kind={glyphKind(item)} size={26} /></div>
        <article>
          <header><span className="transcript-phase">{phase(item)}</span><span>#{item.sequence}</span>
            <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('vi-VN')}</time></header>
          <h3>{labels[item.kind] ?? item.kind}</h3>
          <p className="transcript-meta">{item.actor_name ?? 'Hệ thống'}{item.subject_name ? ` · ${item.subject_name}` : ''} · {item.visibility === 'room' ? 'Công khai trong phòng' : 'Kín trong trận'}</p>
          {summary(item, data.info) && <p className="transcript-summary">{summary(item, data.info)}</p>}
          {item.reason && <p className="transcript-reason"><strong>Lý do:</strong> {item.reason}</p>}
          {item.related_event_ids.length > 0 && <p className="transcript-links">Liên quan: {item.related_event_ids.map((id) => <button key={id} type="button" onClick={() => findEvent(id)} title={id}>#{id.slice(0, 8)}</button>)}</p>}
          <details><summary>Dữ liệu gốc · {item.kind}</summary>
            <p>Mã sự kiện: <code>{item.id}</code>{item.client_request_id ? ` · Mã yêu cầu: ${item.client_request_id}` : ''}</p>
            {Object.keys(item.references).length > 0 && <p>Tham chiếu: {Object.entries(item.references).map(([id, name]) => `${name} (${id})`).join(' · ')}</p>}
            {(item.before !== null || item.after !== null) && <div className="transcript-comparison">
              {item.before !== null && <div><strong>Trước</strong><pre>{JSON.stringify(item.before, null, 2)}</pre></div>}
              {item.after !== null && <div><strong>Sau</strong><pre>{JSON.stringify(item.after, null, 2)}</pre></div>}
            </div>}
            <pre>{JSON.stringify(item.payload, null, 2)}</pre>
          </details>
        </article>
      </li>)}
    </ol>}
    {cursor !== null && <button className="button-secondary transcript-more" type="button" disabled={loading} onClick={() => void more()}>{loading ? 'Đang tải…' : 'Tải thêm sự kiện'}</button>}
  </section>
}
