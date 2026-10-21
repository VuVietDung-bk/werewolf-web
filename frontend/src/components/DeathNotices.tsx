import { useEffect, useState, type FormEvent } from 'react'
import { api, jsonBody, type DeathNotice, type LinkableAnnouncementEvent, type PlayerBoardItem, type PublicAnnouncement, type PublicCorrection, type PublicVoteNotice, type Room } from '../api'
import { useRequestIds } from '../useRequestIds'
import { useConfirmation } from '../useConfirmation'
import StatusGlyph from './StatusGlyph'
import { NoticeGlyph } from './StoryGlyph'

type AnnouncementKind = PublicAnnouncement['kind']
const announcementKinds: { value: AnnouncementKind; label: string }[] = [
  { value: 'general', label: 'Chung' }, { value: 'death', label: 'Cái chết' },
  { value: 'revival', label: 'Hồi sinh' }, { value: 'attack', label: 'Tấn công' },
  { value: 'role', label: 'Role' }, { value: 'vote', label: 'Vote' },
  { value: 'other', label: 'Khác' },
]
const kindName = (kind: AnnouncementKind) => announcementKinds.find((item) => item.value === kind)?.label ?? 'Chung'
const linkName: Record<string, string> = { 'player.kill': 'Giết', 'player.revive': 'Hồi sinh',
  'player.add_effect': 'Thêm hiệu ứng', 'player.remove_effect': 'Gỡ hiệu ứng',
  'player.change_role': 'Đổi role', 'player.change_faction': 'Đổi phe',
  'player.change_team': 'Đổi đội chat', 'vote.decided': 'Quyết định vote' }

type ContentHistoryItem = { sequence: number; source_event_id: string; source_kind: string;
  action: 'edit' | 'delete'; previous_text: string | null; new_text: string | null; created_at: string }
type ContentHistoryPage = { items: ContentHistoryItem[]; next_before_sequence: number | null }
const contentKindName: Record<string, string> = {
  'player.death_announced': 'Thông báo chết', 'player.revival_announced': 'Thông báo hồi sinh',
  'event.public_correction': 'Đính chính', 'event.custom_announcement': 'Thông báo quản trò',
  'vote.result_announced': 'Kết quả vote',
}
const contentGlyphKind: Record<string, PublicAnnouncement['kind'] | 'correction'> = {
  'player.death_announced': 'death', 'player.revival_announced': 'revival',
  'event.public_correction': 'correction', 'event.custom_announcement': 'general',
  'vote.result_announced': 'vote',
}

function NoticeRevisionHistory({ roomId }: { roomId: string }) {
  const [items, setItems] = useState<ContentHistoryItem[]>([])
  const [before, setBefore] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  async function load(cursor?: number) {
    setLoading(true)
    try {
      const page = await api<ContentHistoryPage>(`/api/rooms/${roomId}/game/content/history${cursor ? `?before_sequence=${cursor}` : ''}`)
      setItems((current) => cursor ? [...current, ...page.items] : page.items)
      setBefore(page.next_before_sequence)
      setError('')
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Không tải được lịch sử sửa/xóa.') }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [roomId])
  return <section aria-label="Lịch sử sửa và xóa thông báo">
    <h3>Lịch sử sửa/xóa thông báo sau trận</h3>
    <p>Các bản cũ và thao tác xóa vẫn được lưu, kể cả khi thông báo không còn trong danh sách hiện tại.</p>
    {error && <p className="form-message" role="alert">{error}</p>}
    {items.length > 0 ? <ol className="public-notice-list">{items.map((item) => <li key={item.sequence}>
      <small>Sự kiện #{item.sequence} · <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('vi-VN')}</time></small>
      <strong className="notice-heading"><NoticeGlyph kind={contentGlyphKind[item.source_kind] ?? 'general'} size={20} />
        {contentKindName[item.source_kind] ?? 'Thông báo'} · {item.action === 'edit' ? 'Sửa' : 'Xóa'}</strong>
      <small>Bản gốc: {item.source_event_id}</small>
      <p>Trước: {item.previous_text || '(không có nội dung)'}</p>
      <p>Sau: {item.action === 'delete' ? '(đã xóa)' : item.new_text || '(không có nội dung)'}</p>
    </li>)}</ol> : !loading && !error && <p>Không có thông báo nào được sửa hoặc xóa.</p>}
    {before !== null && <button type="button" className="button-secondary" disabled={loading}
      onClick={() => void load(before)}>{loading ? 'Đang tải…' : 'Xem lịch sử cũ hơn'}</button>}
  </section>
}

export default function DeathNotices({ room, onRoomChanged, onOpenBoard }: {
  room: Room; onRoomChanged: () => Promise<void>; onOpenBoard: () => void
}) {
  const [notices, setNotices] = useState<DeathNotice[]>([])
  const [announcements, setAnnouncements] = useState<PublicAnnouncement[]>([])
  const [corrections, setCorrections] = useState<PublicCorrection[]>([])
  const [voteNotices, setVoteNotices] = useState<PublicVoteNotice[]>([])
  const [pendingPublication, setPendingPublication] = useState<PlayerBoardItem[]>([])
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editCause, setEditCause] = useState('')
  const [newAnnouncement, setNewAnnouncement] = useState('')
  const [announcementKind, setAnnouncementKind] = useState<AnnouncementKind>('general')
  const [relatedUserId, setRelatedUserId] = useState('')
  const [sourceUserId, setSourceUserId] = useState('')
  const [linkedEventId, setLinkedEventId] = useState('')
  const [linkableEvents, setLinkableEvents] = useState<LinkableAnnouncementEvent[]>([])
  const [busy, setBusy] = useState(false)
  const requestIdFor = useRequestIds()
  const { ask, confirmationDialog } = useConfirmation()
  const dead = room.members.filter((member) => !member.public_is_alive)
  const phase = `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}`

  async function load() {
    const [items, publicCorrections, publicVotes, publicAnnouncements, board] = await Promise.all([
      api<DeathNotice[]>(`/api/rooms/${room.id}/game/death-notices`),
      api<PublicCorrection[]>(`/api/rooms/${room.id}/game/events/public-corrections`),
      api<PublicVoteNotice[]>(`/api/rooms/${room.id}/votes/publications`),
      api<PublicAnnouncement[]>(`/api/rooms/${room.id}/game/events/announcements`),
      room.is_host ? api<PlayerBoardItem[]>(`/api/rooms/${room.id}/game/players`) : Promise.resolve([]),
    ])
    setNotices(items)
    setCorrections(publicCorrections)
    setVoteNotices(publicVotes)
    setAnnouncements(publicAnnouncements)
    setPendingPublication(board.filter((item) => item.is_alive !== item.public_is_alive))
    setError('')
  }

  useEffect(() => {
    let active = true
    void Promise.all([
      api<DeathNotice[]>(`/api/rooms/${room.id}/game/death-notices`),
      api<PublicCorrection[]>(`/api/rooms/${room.id}/game/events/public-corrections`),
      api<PublicVoteNotice[]>(`/api/rooms/${room.id}/votes/publications`),
      api<PublicAnnouncement[]>(`/api/rooms/${room.id}/game/events/announcements`),
      room.is_host ? api<PlayerBoardItem[]>(`/api/rooms/${room.id}/game/players`) : Promise.resolve([]),
    ]).then(([items, publicCorrections, publicVotes, publicAnnouncements, board]) => {
      if (active) { setNotices(items); setCorrections(publicCorrections); setVoteNotices(publicVotes); setAnnouncements(publicAnnouncements); setPendingPublication(board.filter((item) => item.is_alive !== item.public_is_alive)); setError('') }
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : 'Không tải được thông báo.')
    })
    return () => { active = false }
  }, [room.id, room.version])

  useEffect(() => {
    if (!room.is_host || room.status !== 'playing') return
    let active = true
    void api<LinkableAnnouncementEvent[]>(`/api/rooms/${room.id}/game/events/announcements/linkable-events`)
      .then((items) => { if (active) setLinkableEvents(items) })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : 'Không tải được thao tác liên kết.') })
    return () => { active = false }
  }, [room.id, room.version, room.is_host, room.status])

  async function publishAnnouncement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = newAnnouncement.trim()
    if (busy || !room.is_host || room.status !== 'playing' || !text) return
    const related = room.members.find((item) => item.user_id === relatedUserId)
    const source = room.members.find((item) => item.user_id === sourceUserId)
    const publicText = [`Loại: ${kindName(announcementKind)}`,
      related ? `Người liên quan: Ghế ${related.seat_number} · ${related.display_name}` : '',
      source ? `Nguồn gây ra: Ghế ${source.seat_number} · ${source.display_name}` : '',
      text].filter(Boolean).join('\n')
    if (!await ask({ title: 'Đăng thông báo', phase, subject: related?.display_name ?? room.name,
      audience: 'Toàn bộ thành viên phòng', publicText,
      note: 'Liên kết thao tác/vote chỉ dùng cho nhật ký; chi tiết kín của liên kết không hiện cho người chơi trong trận.',
      confirmLabel: 'Đăng thông báo' })) return
    const payload = { text, kind: announcementKind, related_user_id: relatedUserId || null,
      source_user_id: sourceUserId || null, linked_event_id: linkedEventId || null }
    const request = requestIdFor({ room_id: room.id, announcement: payload })
    setBusy(true)
    setError('')
    let sent = false
    try {
      await api(`/api/rooms/${room.id}/game/events/announcements`, {
        method: 'POST', body: jsonBody({ client_request_id: request.id, ...payload }),
      })
      sent = true
      request.acknowledge()
      setNewAnnouncement('')
      setAnnouncementKind('general')
      setRelatedUserId('')
      setSourceUserId('')
      setLinkedEventId('')
      await Promise.all([load(), onRoomChanged()])
    } catch (cause) {
      setError(sent ? 'Đã đăng thông báo nhưng chưa tải lại được. Tải lại tab để kiểm tra.'
        : cause instanceof Error ? cause.message : 'Không đăng được thông báo.')
    } finally { setBusy(false) }
  }

  async function changePublicContent(sourceId: string, label: string, currentText: string,
                                     action: 'edit' | 'delete', allowEmpty: boolean) {
    if (busy || !room.is_host || room.status !== 'playing') return
    const nextText = editCause.trim()
    if (action === 'edit' && (nextText === currentText || (!allowEmpty && !nextText))) return
    if (!await ask({ title: action === 'edit' ? 'Đính chính thông báo' : 'Xóa thông báo',
      phase, subject: label, audience: 'Toàn bộ thành viên phòng',
      publicText: action === 'edit'
        ? allowEmpty ? `Nguyên nhân công khai: ${nextText || '(không nêu)'}` : nextText
        : 'Thông báo này sẽ không còn hiển thị trong tab Thông báo.',
      note: 'Bản gốc và thao tác sửa/xóa vẫn được giữ trong sự kiện để đối chiếu sau trận. Xóa thông báo trạng thái không đảo trạng thái sống/chết đã công bố.',
      confirmLabel: action === 'edit' ? 'Lưu sửa đổi' : 'Xóa thông báo' })) return
    const payload = { action, text: action === 'edit' ? nextText : null }
    const request = requestIdFor({ room_id: room.id, source_event_id: sourceId, payload })
    setBusy(true)
    setError('')
    let sent = false
    try {
      await api(`/api/rooms/${room.id}/game/content/${sourceId}/revisions`, {
        method: 'POST', body: jsonBody({ client_request_id: request.id, ...payload }),
      })
      sent = true
      request.acknowledge()
      setEditingId(null)
      setEditCause('')
      await Promise.all([load(), onRoomChanged()])
    } catch (cause) {
      setError(sent ? 'Đã cập nhật thông báo nhưng chưa tải lại được. Tải lại tab để kiểm tra.'
        : cause instanceof Error ? cause.message : 'Không cập nhật được thông báo.')
    } finally { setBusy(false) }
  }

  return <section className="death-notices" aria-labelledby="death-notices-title">
    <span className="eyebrow">THÔNG TIN ĐÃ CÔNG BỐ</span>
    <h2 id="death-notices-title">Thông báo</h2>
    <p>Quản trò đăng diễn biến công khai tại đây. Trạng thái chết/hồi sinh và kết quả vote chỉ xuất hiện khi đã công bố.</p>
    {room.is_host && room.status === 'playing' && pendingPublication.length > 0 && <aside className="pending-publications">
      <h3>Trạng thái kín hoặc chưa công bố ({pendingPublication.length})</h3>
      <p>Chỉ quản trò thấy danh sách này. Khi giữ kín hoặc bỏ qua thông báo, người chơi vẫn thấy trạng thái công khai trước đó.</p>
      <ul>{pendingPublication.map((item) => <li key={item.user_id}>
        <NoticeGlyph kind="sealed" size={20} />
        <span>Ghế {item.seat_number} · {item.display_name}: {item.is_alive ? 'đã hồi sinh' : 'đã chết'} · {
          item.publication_disposition === 'held' ? 'đang giữ kín'
            : item.publication_disposition === 'skipped' ? 'đã bỏ qua thông báo' : 'chờ quyết định'}</span>
      </li>)}</ul>
      <button type="button" className="button-secondary" onClick={onOpenBoard}>Mở Bàn quản trò để công bố</button>
    </aside>}
    {room.is_host && room.status === 'playing' && <form className="stack-form public-announcement-form" onSubmit={(event) => void publishAnnouncement(event)}>
      <h3>Đăng thông báo công khai</h3>
      <label htmlFor="announcement-kind">Loại sự kiện</label>
      <select id="announcement-kind" value={announcementKind}
        onChange={(event) => setAnnouncementKind(event.target.value as AnnouncementKind)}>
        {announcementKinds.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
      </select>
      <p className="announcement-kind-preview"><NoticeGlyph kind={announcementKind} size={24} />
        Loại thẻ: {kindName(announcementKind)}</p>
      <small>Loại chỉ phân loại thẻ thông báo; muốn đổi trạng thái sống/chết công khai, dùng Bàn quản trò.</small>
      <label htmlFor="announcement-related">Người liên quan (tùy chọn)</label>
      <select id="announcement-related" value={relatedUserId} onChange={(event) => setRelatedUserId(event.target.value)}>
        <option value="">Không nêu tên</option>
        {room.members.map((member) => <option key={member.user_id} value={member.user_id}>
          Ghế {member.seat_number} · {member.display_name}</option>)}
      </select>
      <label htmlFor="announcement-source">Nguồn gây ra (tùy chọn, sẽ công khai)</label>
      <select id="announcement-source" value={sourceUserId} onChange={(event) => setSourceUserId(event.target.value)}>
        <option value="">Giữ kín/không nêu tên</option>
        {room.members.map((member) => <option key={member.user_id} value={member.user_id}>
          Ghế {member.seat_number} · {member.display_name}</option>)}
      </select>
      <label htmlFor="announcement-link">Liên kết thao tác hoặc quyết định vote (tùy chọn, chỉ nhật ký)</label>
      <select id="announcement-link" value={linkedEventId} onChange={(event) => setLinkedEventId(event.target.value)}>
        <option value="">Không liên kết</option>
        {linkableEvents.map((item) => <option key={item.event_id} value={item.event_id}>
          #{item.sequence} · {linkName[item.kind] ?? item.kind}{item.target_name ? ` · ${item.target_name}` : ''}</option>)}
      </select>
      <label htmlFor="new-announcement">Nội dung mọi thành viên sẽ thấy</label>
      <textarea id="new-announcement" maxLength={2000} value={newAnnouncement}
        onChange={(event) => setNewAnnouncement(event.target.value)}
        placeholder="Ví dụ: Một sự kiện bất ngờ vừa xảy ra trong làng." />
      <button disabled={busy || !newAnnouncement.trim()}>Xem trước và đăng</button>
    </form>}
    <h3>Đang được công bố là đã chết</h3>
    {dead.length ? <ul className="public-dead-list">{dead.map((member) => <li key={member.user_id}>
      <strong>Ghế {member.seat_number} · {member.display_name}</strong>
      {member.public_role_name && <span>Role đã công bố: {member.public_role_name}</span>}
    </li>)}</ul> : <p>Chưa có ai được công bố là đã chết.</p>}
    <h3>Lịch sử thông báo</h3>
    {error && <p className="form-message" role="alert">{error}</p>}
    {notices.length ? <ol className="public-notice-list">{notices.map((item) =>
      <li key={item.id} className={item.status === 'dead' ? 'notice-death' : 'notice-revival'}>
        <small>{item.phase_type === 'night' ? 'Đêm' : 'Ngày'} {item.round_number} · <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('vi-VN')}</time></small>
        <strong className="notice-heading"><StatusGlyph kind={item.status === 'dead' ? 'dead' : 'revived'} size={20} />
          {item.status === 'dead' ? 'Đã công bố chết' : 'Đã công bố hồi sinh'}: {item.display_name} (ghế {item.seat_number})</strong>
        {item.edited_at && <small>Đã sửa</small>}
        {editingId === item.id ? <form className="stack-form inline-edit-form"
          onSubmit={(event) => { event.preventDefault(); void changePublicContent(item.id,
            `thông báo về ${item.display_name}`, item.public_cause ?? '', 'edit', true) }}>
          <label htmlFor={`edit-notice-${item.id}`}>Nguyên nhân công khai (để trống nếu muốn bỏ)</label>
          <textarea id={`edit-notice-${item.id}`} maxLength={500} value={editCause}
            onChange={(event) => setEditCause(event.target.value)} />
          <div className="inline-actions"><button disabled={busy || editCause.trim() === (item.public_cause ?? '')}>Lưu sửa đổi</button>
            <button type="button" className="button-secondary" onClick={() => setEditingId(null)}>Hủy</button></div>
        </form> : item.public_cause && <p>Nguyên nhân: {item.public_cause}</p>}
        {item.revealed_role_name && <p>Role công bố: {item.revealed_role_name}</p>}
        {room.is_host && room.status === 'playing' && editingId !== item.id && <div className="inline-actions">
          <button type="button" className="button-secondary" disabled={busy}
            onClick={() => { setEditingId(item.id); setEditCause(item.public_cause ?? '') }}>Sửa</button>
          <button type="button" className="button-secondary" disabled={busy}
            onClick={() => void changePublicContent(item.id, `thông báo về ${item.display_name}`,
              item.public_cause ?? '', 'delete', true)}>Xóa</button>
        </div>}
      </li>)}</ol> : !error && <p>Chưa có thông báo.</p>}
    {voteNotices.length > 0 && <><h3>Kết quả vote đã công bố</h3>
      <ol className="public-notice-list">{voteNotices.map((item) => <li key={item.id} className="notice-vote">
        <small>Ngày {item.day_number} · <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('vi-VN')}</time></small>
        <strong className="notice-heading"><NoticeGlyph kind="vote" size={20} />Kết quả vote</strong>{item.edited_at && <small>Đã sửa</small>}
        {editingId === item.id ? <form className="stack-form inline-edit-form"
          onSubmit={(event) => { event.preventDefault(); void changePublicContent(item.id,
            `kết quả vote Ngày ${item.day_number}`, item.text, 'edit', false) }}>
          <label htmlFor={`edit-vote-${item.id}`}>Nội dung công khai</label>
          <textarea id={`edit-vote-${item.id}`} maxLength={2000} value={editCause}
            onChange={(event) => setEditCause(event.target.value)} />
          <div className="inline-actions"><button disabled={busy || !editCause.trim() || editCause.trim() === item.text}>Lưu sửa đổi</button>
            <button type="button" className="button-secondary" onClick={() => setEditingId(null)}>Hủy</button></div>
        </form> : <p>{item.text}</p>}
        {room.is_host && room.status === 'playing' && editingId !== item.id && <div className="inline-actions">
          <button type="button" className="button-secondary" disabled={busy}
            onClick={() => { setEditingId(item.id); setEditCause(item.text) }}>Sửa</button>
          <button type="button" className="button-secondary" disabled={busy}
            onClick={() => void changePublicContent(item.id, `kết quả vote Ngày ${item.day_number}`,
              item.text, 'delete', false)}>Xóa</button>
        </div>}
      </li>)}</ol></>}
    {announcements.length > 0 && <><h3>Thông báo từ quản trò</h3>
      <ol className="public-notice-list">{announcements.map((item) => <li key={item.id} className={`notice-custom notice-kind-${item.kind}`}>
        <small>{item.phase_type === 'night' ? 'Đêm' : 'Ngày'} {item.round_number} · <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('vi-VN')}</time></small>
        <strong className="notice-heading"><NoticeGlyph kind={item.kind} size={20} />Loại: {kindName(item.kind)}</strong>{item.edited_at && <small>Đã sửa</small>}
        {item.related_player && <small>Người liên quan: Ghế {item.related_player.seat_number} · {item.related_player.display_name}</small>}
        {item.source_player && <small>Nguồn gây ra: Ghế {item.source_player.seat_number} · {item.source_player.display_name}</small>}
        {editingId === item.id ? <form className="stack-form inline-edit-form"
          onSubmit={(event) => { event.preventDefault(); void changePublicContent(item.id,
            'thông báo của quản trò', item.text, 'edit', false) }}>
          <label htmlFor={`edit-announcement-${item.id}`}>Nội dung đính chính</label>
          <textarea id={`edit-announcement-${item.id}`} maxLength={2000} value={editCause}
            onChange={(event) => setEditCause(event.target.value)} />
          <div className="inline-actions"><button disabled={busy || !editCause.trim() || editCause.trim() === item.text}>Lưu sửa đổi</button>
            <button type="button" className="button-secondary" onClick={() => setEditingId(null)}>Hủy</button></div>
        </form> : <p>{item.text}</p>}
        {room.is_host && room.status === 'playing' && editingId !== item.id && <div className="inline-actions">
          <button type="button" className="button-secondary" disabled={busy}
            onClick={() => { setEditingId(item.id); setEditCause(item.text) }}>Sửa</button>
          <button type="button" className="button-secondary" disabled={busy}
            onClick={() => void changePublicContent(item.id, 'thông báo của quản trò',
              item.text, 'delete', false)}>Xóa</button>
        </div>}
      </li>)}</ol></>}
    {corrections.length > 0 && <><h3>Đính chính công khai</h3>
      <ol className="public-notice-list">{corrections.map((item) => <li key={item.id}>
        <small><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('vi-VN')}</time> · Sự kiện #{item.source_sequence}</small>
        <strong className="notice-heading"><NoticeGlyph kind="correction" size={20} />Đính chính</strong>{item.edited_at && <small>Đã sửa</small>}
        {editingId === item.id ? <form className="stack-form inline-edit-form"
          onSubmit={(event) => { event.preventDefault(); void changePublicContent(item.id,
            'đính chính', item.text, 'edit', false) }}>
          <label htmlFor={`edit-correction-${item.id}`}>Nội dung đính chính</label>
          <textarea id={`edit-correction-${item.id}`} maxLength={2000} value={editCause}
            onChange={(event) => setEditCause(event.target.value)} />
          <div className="inline-actions"><button disabled={busy || !editCause.trim() || editCause.trim() === item.text}>Lưu sửa đổi</button>
            <button type="button" className="button-secondary" onClick={() => setEditingId(null)}>Hủy</button></div>
        </form> : <p>{item.text}</p>}
        {room.is_host && room.status === 'playing' && editingId !== item.id && <div className="inline-actions">
          <button type="button" className="button-secondary" disabled={busy}
            onClick={() => { setEditingId(item.id); setEditCause(item.text) }}>Sửa</button>
          <button type="button" className="button-secondary" disabled={busy}
            onClick={() => void changePublicContent(item.id, 'đính chính', item.text, 'delete', false)}>Xóa</button>
        </div>}
      </li>)}</ol></>}
    {room.status === 'finished' && <NoticeRevisionHistory roomId={room.id} />}
    {confirmationDialog}
  </section>
}
