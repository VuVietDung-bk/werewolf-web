import { useEffect, useState, type FormEvent } from 'react'
import { api, jsonBody, type DeathNotice, type PublicAnnouncement, type PublicCorrection, type PublicVoteNotice, type Room } from '../api'
import { useRequestIds } from '../useRequestIds'
import { useConfirmation } from '../useConfirmation'
import StatusGlyph from './StatusGlyph'

export default function DeathNotices({ room, onRoomChanged }: {
  room: Room; onRoomChanged: () => Promise<void>
}) {
  const [notices, setNotices] = useState<DeathNotice[]>([])
  const [announcements, setAnnouncements] = useState<PublicAnnouncement[]>([])
  const [corrections, setCorrections] = useState<PublicCorrection[]>([])
  const [voteNotices, setVoteNotices] = useState<PublicVoteNotice[]>([])
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editCause, setEditCause] = useState('')
  const [newAnnouncement, setNewAnnouncement] = useState('')
  const [busy, setBusy] = useState(false)
  const requestIdFor = useRequestIds()
  const { ask, confirmationDialog } = useConfirmation()
  const dead = room.members.filter((member) => !member.public_is_alive)
  const phase = `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}`

  async function load() {
    const [items, publicCorrections, publicVotes, publicAnnouncements] = await Promise.all([
      api<DeathNotice[]>(`/api/rooms/${room.id}/game/death-notices`),
      api<PublicCorrection[]>(`/api/rooms/${room.id}/game/events/public-corrections`),
      api<PublicVoteNotice[]>(`/api/rooms/${room.id}/votes/publications`),
      api<PublicAnnouncement[]>(`/api/rooms/${room.id}/game/events/announcements`),
    ])
    setNotices(items)
    setCorrections(publicCorrections)
    setVoteNotices(publicVotes)
    setAnnouncements(publicAnnouncements)
    setError('')
  }

  useEffect(() => {
    let active = true
    void Promise.all([
      api<DeathNotice[]>(`/api/rooms/${room.id}/game/death-notices`),
      api<PublicCorrection[]>(`/api/rooms/${room.id}/game/events/public-corrections`),
      api<PublicVoteNotice[]>(`/api/rooms/${room.id}/votes/publications`),
      api<PublicAnnouncement[]>(`/api/rooms/${room.id}/game/events/announcements`),
    ]).then(([items, publicCorrections, publicVotes, publicAnnouncements]) => {
      if (active) { setNotices(items); setCorrections(publicCorrections); setVoteNotices(publicVotes); setAnnouncements(publicAnnouncements); setError('') }
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : 'Không tải được thông báo.')
    })
    return () => { active = false }
  }, [room.id, room.version])

  async function publishAnnouncement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const text = newAnnouncement.trim()
    if (busy || !room.is_host || room.status !== 'playing' || !text) return
    if (!await ask({ title: 'Đăng thông báo', phase, subject: room.name,
      audience: 'Toàn bộ thành viên phòng', publicText: text,
      note: 'Thông báo sẽ xuất hiện trong tab này và được lưu vào sự kiện trận.',
      confirmLabel: 'Đăng thông báo' })) return
    const request = requestIdFor({ room_id: room.id, announcement: text })
    setBusy(true)
    setError('')
    let sent = false
    try {
      await api(`/api/rooms/${room.id}/game/events/announcements`, {
        method: 'POST', body: jsonBody({ client_request_id: request.id, text }),
      })
      sent = true
      request.acknowledge()
      setNewAnnouncement('')
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
    {room.is_host && room.status === 'playing' && <form className="stack-form public-announcement-form" onSubmit={(event) => void publishAnnouncement(event)}>
      <h3>Đăng thông báo công khai</h3>
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
        <strong>Kết quả vote</strong>{item.edited_at && <small>Đã sửa</small>}
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
      <ol className="public-notice-list">{announcements.map((item) => <li key={item.id} className="notice-custom">
        <small>{item.phase_type === 'night' ? 'Đêm' : 'Ngày'} {item.round_number} · <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('vi-VN')}</time></small>
        <strong>Thông báo công khai</strong>{item.edited_at && <small>Đã sửa</small>}
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
        <strong>Đính chính</strong>{item.edited_at && <small>Đã sửa</small>}
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
    {confirmationDialog}
  </section>
}
