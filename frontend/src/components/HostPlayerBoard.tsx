import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, jsonBody, type PlayerBoardItem, type PlayerNote, type Role, type RoleCatalog, type Room } from '../api'
import RoleIcon from './RoleIcon'
import StatusGlyph from './StatusGlyph'
import PlayerIdentityControls from './PlayerIdentityControls'
import { HostPrivateMessages } from './PrivateInbox'
import { factionClass } from '../factions'
import { useRequestIds } from '../useRequestIds'
import { useConfirmation } from '../useConfirmation'

type Action = 'kill' | 'revive' | 'add_effect' | 'remove_effect' | 'note'
type StatusFilter = 'all' | 'alive' | 'dead' | 'pending_publication'

export default function HostPlayerBoard({ room, onRoomChanged }: {
  room: Room; onRoomChanged: () => Promise<void>
}) {
  const [players, setPlayers] = useState<PlayerBoardItem[]>([])
  const [notes, setNotes] = useState<PlayerNote[]>([])
  const [roles, setRoles] = useState<Role[]>([])
  const [modalTab, setModalTab] = useState<'status' | 'identity' | 'message'>('status')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [filter, setFilter] = useState<StatusFilter>('all')
  const [factionFilter, setFactionFilter] = useState('')
  const [effectFilter, setEffectFilter] = useState('')
  const [pendingRequestsOnly, setPendingRequestsOnly] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reason, setReason] = useState('')
  const [publicationReason, setPublicationReason] = useState('')
  const [effect, setEffect] = useState('')
  const [note, setNote] = useState('')
  const [editingNoteId, setEditingNoteId] = useState<string | null>(null)
  const [editNoteText, setEditNoteText] = useState('')
  const [publicCause, setPublicCause] = useState('')
  const [revealRole, setRevealRole] = useState(false)
  const dialog = useRef<HTMLDialogElement>(null)
  const requestIdFor = useRequestIds()
  const { ask, confirmationDialog } = useConfirmation()
  const selected = players.find((item) => item.user_id === selectedId)

  async function refresh() {
    setPlayers(await api<PlayerBoardItem[]>(`/api/rooms/${room.id}/game/players`))
  }

  useEffect(() => {
    void refresh().catch((cause) => setError(cause instanceof Error ? cause.message : 'Không tải được bảng người chơi.'))
    const timer = window.setInterval(() => {
      void refresh().catch((cause) => setError(cause instanceof Error ? cause.message : 'Không cập nhật được bảng người chơi.'))
    }, 5000)
    return () => window.clearInterval(timer)
  }, [room.id, room.version])

  useEffect(() => {
    void api<RoleCatalog>(`/api/rooms/${room.id}/role-catalog`).then((catalog) => setRoles(catalog.roles))
      .catch((cause) => setError(cause instanceof Error ? cause.message : 'Không tải được danh mục role.'))
  }, [room.id])

  useEffect(() => {
    if (factionFilter && players.length && !players.some((item) => item.role.faction_label === factionFilter)) {
      setFactionFilter('')
    }
    if (effectFilter && !players.some((item) => item.effects.includes(effectFilter))) {
      setEffectFilter('')
    }
  }, [players, factionFilter, effectFilter])

  useEffect(() => {
    if (selectedId) {
      dialog.current?.showModal()
      void api<PlayerNote[]>(`/api/rooms/${room.id}/game/players/${selectedId}/notes`)
        .then(setNotes).catch((cause) => setError(cause instanceof Error ? cause.message : 'Không tải được ghi chú.'))
    } else {
      dialog.current?.close()
      setNotes([])
      setEditingNoteId(null)
      setEditNoteText('')
      setError('')
    }
  }, [selectedId, room.id])

  function close() {
    dialog.current?.close()
    setSelectedId(null)
    setReason('')
    setPublicationReason('')
    setEffect('')
    setNote('')
    setEditingNoteId(null)
    setEditNoteText('')
    setPublicCause('')
    setRevealRole(false)
    setModalTab('status')
  }

  async function submit(action: Action, text?: string) {
    if (!selected || busy) return
    const actualReason = action === 'note' ? null : reason.trim()
    if (action !== 'note' && !actualReason) return
    if (action === 'kill' || action === 'revive') {
      if (!await ask({ title: action === 'kill' ? 'Đánh dấu chết' : 'Đánh dấu hồi sinh',
        phase: `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}`,
        subject: `Ghế ${selected.seat_number} · ${selected.display_name}`,
        audience: 'Chỉ quản trò trong trận',
        note: `Lý do quản trò lưu: ${actualReason}. Trạng thái vẫn kín đến khi quản trò công bố riêng.`,
        confirmLabel: action === 'kill' ? 'Đánh dấu chết' : 'Đánh dấu hồi sinh' })) return
    }
    setBusy(true)
    setError('')
    const payload = { action, text: text ?? null, reason: actualReason }
    const request = requestIdFor({ room_id: room.id, player_id: selected.user_id, payload })
    let sent = false
    try {
      await api(`/api/rooms/${room.id}/game/players/${selected.user_id}/actions`, {
        method: 'POST', body: jsonBody({ client_request_id: request.id, ...payload }),
      })
      sent = true
      request.acknowledge()
      if (action === 'note') {
        setNote('')
      } else {
        setReason('')
        setEffect('')
      }
      await refresh()
      if (action === 'note') {
        setNotes(await api<PlayerNote[]>(`/api/rooms/${room.id}/game/players/${selected.user_id}/notes`))
      }
    } catch (cause) {
      setError(sent ? 'Đã ghi thao tác nhưng chưa tải lại được bảng. Mở lại tab để kiểm tra.'
        : cause instanceof Error ? cause.message : 'Không thực hiện được thao tác.')
      if (!sent) await refresh().catch(() => {})
    } finally { setBusy(false) }
  }

  function addEffect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (effect.trim()) void submit('add_effect', effect.trim())
  }

  function addNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (note.trim()) void submit('note', note.trim())
  }

  async function changeNote(item: PlayerNote, action: 'edit' | 'delete') {
    if (!selected || busy || room.status !== 'playing') return
    const nextText = editNoteText.trim()
    if (action === 'edit' && (!nextText || nextText === item.text)) return
    if (action === 'delete' && !window.confirm('Xóa ghi chú này khỏi bảng? Bản gốc và thao tác xóa vẫn được giữ để đối chiếu sau trận.')) return
    const payload = { action, text: action === 'edit' ? nextText : null }
    const request = requestIdFor({ room_id: room.id, note_id: item.id, payload })
    setBusy(true)
    setError('')
    let sent = false
    try {
      await api(`/api/rooms/${room.id}/game/content/notes/${item.id}/revisions`, {
        method: 'POST', body: jsonBody({ client_request_id: request.id, ...payload }),
      })
      sent = true
      request.acknowledge()
      setEditingNoteId(null)
      setEditNoteText('')
      await Promise.all([
        refresh(),
        api<PlayerNote[]>(`/api/rooms/${room.id}/game/players/${selected.user_id}/notes`).then(setNotes),
      ])
    } catch (cause) {
      setError(sent ? 'Đã cập nhật ghi chú nhưng chưa tải lại được. Mở lại bảng để kiểm tra.'
        : cause instanceof Error ? cause.message : 'Không cập nhật được ghi chú.')
    } finally { setBusy(false) }
  }

  async function announce(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected || busy || !publicationReason.trim() || selected.is_alive === selected.public_is_alive) return
    const status = selected.is_alive ? 'hồi sinh' : 'đã chết'
    const publicText = `Ghế ${selected.seat_number} · ${selected.display_name}: ${status}`
      + (publicCause.trim() ? `\nNguyên nhân: ${publicCause.trim()}` : '')
      + (revealRole ? `\nRole: ${selected.role.name}` : '')
    if (!await ask({ title: selected.is_alive ? 'Công bố hồi sinh' : 'Công bố người chết',
      phase: `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}`,
      subject: `Ghế ${selected.seat_number} · ${selected.display_name}`,
      audience: 'Toàn bộ thành viên phòng', publicText,
      note: 'Trạng thái công khai của người chơi sẽ đổi ngay. Lý do quyết định của quản trò vẫn kín trong trận.',
      confirmLabel: 'Công bố cho toàn phòng' })) return
    setBusy(true)
    setError('')
    const payload = { moderator_reason: publicationReason.trim(), public_cause: publicCause.trim() || null,
      reveal_role: revealRole }
    const request = requestIdFor({ room_id: room.id, player_id: selected.user_id, announce: payload })
    let sent = false
    try {
      await api(`/api/rooms/${room.id}/game/players/${selected.user_id}/announce`, {
        method: 'POST', body: jsonBody({ client_request_id: request.id, ...payload }),
      })
      sent = true
      request.acknowledge()
      setPublicationReason('')
      setPublicCause('')
      setRevealRole(false)
      await Promise.all([refresh(), onRoomChanged()])
    } catch (cause) {
      setError(sent ? 'Đã công bố nhưng chưa tải lại được bảng. Mở lại tab để kiểm tra.'
        : cause instanceof Error ? cause.message : 'Không công bố được trạng thái.')
      if (!sent) await refresh().catch(() => {})
    } finally { setBusy(false) }
  }

  async function decidePublication(decision: 'hold' | 'skip') {
    if (!selected || busy || !publicationReason.trim() || selected.is_alive === selected.public_is_alive) return
    const subject = `Ghế ${selected.seat_number} · ${selected.display_name}`
    const label = decision === 'hold' ? 'Giữ kín trạng thái' : 'Bỏ qua thông báo trạng thái'
    if (!await ask({ title: label,
      phase: `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}`,
      subject, audience: 'Chỉ quản trò trong trận',
      note: `Lý do lưu trong nhật ký: ${publicationReason.trim()}. Danh sách ghế và tab Thông báo của người chơi vẫn giữ trạng thái cũ. Quản trò có thể công bố riêng sau nếu đổi quyết định.`,
      confirmLabel: decision === 'hold' ? 'Giữ kín' : 'Bỏ qua thông báo' })) return
    const payload = { decision, reason: publicationReason.trim() }
    const request = requestIdFor({ room_id: room.id, player_id: selected.user_id,
      publication_disposition: payload })
    setBusy(true)
    setError('')
    let sent = false
    try {
      await api(`/api/rooms/${room.id}/game/players/${selected.user_id}/publication-disposition`, {
        method: 'POST', body: jsonBody({ client_request_id: request.id, ...payload }),
      })
      sent = true
      request.acknowledge()
      setPublicationReason('')
      await refresh()
    } catch (cause) {
      setError(sent ? 'Đã ghi quyết định nhưng chưa tải lại được bảng. Mở lại tab để kiểm tra.'
        : cause instanceof Error ? cause.message : 'Không ghi được quyết định thông báo.')
      if (!sent) await refresh().catch(() => {})
    } finally { setBusy(false) }
  }

  const factions = [...new Set(players.map((item) => item.role.faction_label))].sort((a, b) => a.localeCompare(b, 'vi'))
  const effects = [...new Set(players.flatMap((item) => item.effects))].sort((a, b) => a.localeCompare(b, 'vi'))
  const pendingPublication = players.filter((item) => item.is_alive !== item.public_is_alive).length
  const pendingRequests = players.reduce((sum, item) => sum + item.pending_request_count, 0)
  const shown = players.filter((item) => {
    const matchesStatus = filter === 'all' || (filter === 'alive' && item.is_alive)
      || (filter === 'dead' && !item.is_alive)
      || (filter === 'pending_publication' && item.is_alive !== item.public_is_alive)
    return matchesStatus && (!pendingRequestsOnly || item.pending_request_count > 0)
      && (!factionFilter || item.role.faction_label === factionFilter)
      && (!effectFilter || item.effects.includes(effectFilter))
  })
  return <section className="host-board" aria-labelledby="host-board-title">
    <div className={`phase-banner board-phase-banner phase-${room.phase_type}`}>
      <span className="eyebrow">CHỈ QUẢN TRÒ · {room.phase_type === 'night' ? 'ĐÊM' : 'NGÀY'} {room.round_number}</span>
      <h2 id="host-board-title">Bảng người chơi</h2>
    </div>
    <p>Trạng thái sống/chết, hiệu ứng và ghi chú ở đây là thông tin kín. Thao tác được ghi vào nhật ký trận.</p>
    <div className="board-filters" role="group" aria-label="Lọc trạng thái">
      {([['all', 'Tất cả'], ['alive', 'Đang sống'], ['dead', 'Đã chết'],
        ['pending_publication', `Chờ công bố (${pendingPublication})`]] as const).map(([value, label]) =>
        <button key={value} type="button" className={filter === value ? '' : 'button-secondary'}
          aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}
    </div>
    <div className="board-filters board-request-filter" role="group" aria-label="Lọc yêu cầu role">
      <button type="button" className={pendingRequestsOnly ? '' : 'button-secondary'}
        aria-pressed={pendingRequestsOnly} onClick={() => setPendingRequestsOnly((current) => !current)}>
        Có yêu cầu chờ ({pendingRequests})
      </button>
      <small>Gồm chờ xử lý và đang xét. Bảng tự cập nhật mỗi 5 giây.</small>
    </div>
    <div className="board-filter-selects">
      <label>Phe
        <select value={factionFilter} onChange={(event) => setFactionFilter(event.target.value)}>
          <option value="">Tất cả phe</option>
          {factions.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>
      <label>Hiệu ứng
        <select value={effectFilter} onChange={(event) => setEffectFilter(event.target.value)}>
          <option value="">Tất cả hiệu ứng</option>
          {effects.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>
    </div>
    <p className="board-count" aria-live="polite">Hiển thị {shown.length}/{players.length} người chơi.</p>
    <ul className="board-list">{shown.map((item) => <li key={item.user_id} className="board-player">
      <div><small>GHẾ {item.seat_number.toString().padStart(2, '0')}</small><strong>{item.display_name}</strong>
        <span className={item.is_alive ? 'state-alive' : 'state-dead'}>
          {!item.is_alive && <StatusGlyph kind="dead" size={17} />}
          {item.is_alive ? 'Đang sống' : 'Đã chết'}{item.is_alive !== item.public_is_alive
            ? ` · ${item.publication_disposition === 'held' ? 'đang giữ kín'
              : item.publication_disposition === 'skipped' ? 'đã bỏ qua thông báo' : 'chờ quyết định'}`
            : !item.is_alive ? ' · đã công bố' : ''}
        </span></div>
      <div className="board-role"><RoleIcon role={item.role} roomId={room.id} size={32} />
        <span>{item.role.name}</span><span className={`faction-badge ${factionClass(item.role.faction_label)}`}>{item.role.faction_label}</span></div>
      <div className="board-summary">{item.effects.length ? <><StatusGlyph kind="effect" size={18} />Hiệu ứng: {item.effects.join(', ')}</> : 'Không có hiệu ứng'} · {item.note_count} ghi chú
        {item.pending_request_count > 0 && <span className="board-request-count">
          {item.pending_request_count} yêu cầu role đang chờ
        </span>}
      </div>
      <button type="button" className="button-secondary" onClick={() => setSelectedId(item.user_id)}>Thao tác</button>
    </li>)}</ul>
    {players.length > 0 && shown.length === 0 && <p>Không có người chơi phù hợp bộ lọc.</p>}
    {!players.length && !error && <p>Đang tải người chơi…</p>}
    {error && !selectedId && <p className="form-message" role="alert">{error}</p>}

    <dialog ref={dialog} className="player-dialog" onClose={close}>
      {selected && <div className="player-dialog-content">
        <header><div><span className="eyebrow">GHẾ {selected.seat_number}</span>
          <h3>{selected.display_name}</h3><p>{selected.role.name} · {selected.role.faction_label} · {selected.is_alive ? 'Đang sống' : 'Đã chết'}</p></div>
          <button type="button" className="button-secondary" onClick={close} aria-label="Đóng">✕</button></header>
        <div className="player-dialog-tabs" role="group" aria-label="Thao tác với người chơi">
          {([['status', 'Trạng thái'], ['identity', 'Role và phe'], ['message', 'Tin riêng']] as const).map(([tab, label]) =>
            <button type="button" key={tab} className={modalTab === tab ? '' : 'button-secondary'}
              aria-pressed={modalTab === tab} onClick={() => setModalTab(tab)}>{label}</button>)}
        </div>
        {modalTab === 'status' && <>{room.status === 'playing' ? <>
          <div className="stack-form player-action-block">
            <label htmlFor="player-action-reason">Lý do thao tác kín</label>
            <textarea id="player-action-reason" value={reason} maxLength={1000}
              onChange={(event) => setReason(event.target.value)} placeholder="Lý do sẽ xuất hiện trong nhật ký sau trận" />
            <div className="game-control-actions">
              <button type="button" disabled={busy || room.is_paused || !reason.trim() || !selected.is_alive}
                onClick={() => void submit('kill')}>Giết</button>
              <button type="button" className="button-secondary" disabled={busy || room.is_paused || !reason.trim() || selected.is_alive}
                onClick={() => void submit('revive')}>Hồi sinh</button>
            </div>
          </div>
          {selected.is_alive !== selected.public_is_alive && <form className="stack-form player-action-block announce-form" onSubmit={(event) => void announce(event)}>
            <h4>Xử lý thông báo {selected.is_alive ? 'hồi sinh' : 'người chết'}</h4>
            <p>Người chơi khác vẫn thấy trạng thái cũ. Giữ kín hoặc bỏ qua không đăng thẻ và không đổi danh sách công khai; quản trò có thể công bố sau bằng lệnh riêng.</p>
            <p>Hiện tại: {selected.publication_disposition === 'held' ? 'đang giữ kín'
              : selected.publication_disposition === 'skipped' ? 'đã bỏ qua thông báo' : 'chờ quyết định'}</p>
            <label htmlFor="publication-reason">Lý do xử lý thông báo (kín)</label>
            <textarea id="publication-reason" value={publicationReason} maxLength={1000}
              onChange={(event) => setPublicationReason(event.target.value)} />
            <label htmlFor="public-cause">Nguyên nhân công khai (tùy chọn)</label>
            <input id="public-cause" value={publicCause} maxLength={500}
              onChange={(event) => setPublicCause(event.target.value)} />
            <label className="consent"><input type="checkbox" checked={revealRole}
              onChange={(event) => setRevealRole(event.target.checked)} />
              <span>Công bố role của người này</span></label>
            <div className="game-control-actions">
              <button type="button" className="button-secondary" disabled={busy || !publicationReason.trim() || selected.publication_disposition === 'held'}
                onClick={() => void decidePublication('hold')}>Giữ kín</button>
              <button type="button" className="button-secondary" disabled={busy || !publicationReason.trim() || selected.publication_disposition === 'skipped'}
                onClick={() => void decidePublication('skip')}>Bỏ qua thông báo</button>
              <button type="submit" disabled={busy || !publicationReason.trim()}>Công bố cho toàn phòng</button>
            </div>
          </form>}
          <form className="stack-form player-action-block" onSubmit={addEffect}>
            <label htmlFor="player-effect">Thêm hiệu ứng</label>
            <input id="player-effect" value={effect} maxLength={80} onChange={(event) => setEffect(event.target.value)} />
            <button type="submit" disabled={busy || room.is_paused || !effect.trim() || !reason.trim()}>Thêm hiệu ứng</button>
          </form>
          {selected.effects.length > 0 && <div className="player-effects"><strong>Hiệu ứng hiện tại</strong>
            {selected.effects.map((value) => <div key={value}><span>{value}</span>
              <button type="button" className="button-secondary" disabled={busy || room.is_paused || !reason.trim()}
                onClick={() => void submit('remove_effect', value)}>Gỡ</button></div>)}</div>}
          <form className="stack-form player-action-block" onSubmit={addNote}>
            <label htmlFor="player-note">Ghi chú quản trò</label>
            <textarea id="player-note" value={note} maxLength={2000}
              onChange={(event) => setNote(event.target.value)} />
            <button type="submit" className="button-secondary" disabled={busy || !note.trim()}>Lưu ghi chú</button>
          </form>
        </> : null}
        <div className="player-notes"><h4>Ghi chú gần đây</h4>
          {notes.length ? <ol>{notes.map((item) => <li key={item.id}>
            <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('vi-VN')}</time>
            {item.edited_at && <small> · Đã sửa</small>}
            {editingNoteId === item.id ? <form className="stack-form inline-edit-form"
              onSubmit={(event) => { event.preventDefault(); void changeNote(item, 'edit') }}>
              <label htmlFor={`edit-note-${item.id}`}>Sửa ghi chú</label>
              <textarea id={`edit-note-${item.id}`} maxLength={2000} value={editNoteText}
                onChange={(event) => setEditNoteText(event.target.value)} />
              <div className="inline-actions"><button disabled={busy || !editNoteText.trim() || editNoteText.trim() === item.text}>Lưu</button>
                <button type="button" className="button-secondary" onClick={() => setEditingNoteId(null)}>Hủy</button></div>
            </form> : <p>{item.text}</p>}
            {room.status === 'playing' && editingNoteId !== item.id && <div className="inline-actions">
              <button type="button" className="button-secondary" disabled={busy}
                onClick={() => { setEditingNoteId(item.id); setEditNoteText(item.text) }}>Sửa</button>
              <button type="button" className="button-secondary" disabled={busy}
                onClick={() => void changeNote(item, 'delete')}>Xóa</button>
            </div>}
          </li>)}</ol> : <p>Chưa có ghi chú.</p>}
        </div></>}
        {modalTab === 'identity' && <PlayerIdentityControls
          key={`${selected.user_id}:${selected.role.id}:${selected.role.faction_label}:${selected.role.team_chat_label ?? ''}`}
          room={room} player={selected} roles={roles} onChanged={refresh} />}
        {modalTab === 'message' && <HostPrivateMessages key={selected.user_id} room={room} playerId={selected.user_id} />}
        {error && <p className="form-message" role="alert">{error}</p>}
      </div>}
    </dialog>
    {confirmationDialog}
  </section>
}
