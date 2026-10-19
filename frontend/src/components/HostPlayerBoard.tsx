import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, jsonBody, type PlayerBoardItem, type PlayerNote, type Room } from '../api'
import RoleIcon from './RoleIcon'
import { factionClass } from '../factions'

type Action = 'kill' | 'revive' | 'add_effect' | 'remove_effect' | 'note'

export default function HostPlayerBoard({ room }: { room: Room }) {
  const [players, setPlayers] = useState<PlayerBoardItem[]>([])
  const [notes, setNotes] = useState<PlayerNote[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'alive' | 'dead'>('all')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reason, setReason] = useState('')
  const [effect, setEffect] = useState('')
  const [note, setNote] = useState('')
  const dialog = useRef<HTMLDialogElement>(null)
  const selected = players.find((item) => item.user_id === selectedId)

  async function refresh() {
    setPlayers(await api<PlayerBoardItem[]>(`/api/rooms/${room.id}/game/players`))
  }

  useEffect(() => {
    void refresh().catch((cause) => setError(cause instanceof Error ? cause.message : 'Không tải được bảng người chơi.'))
  }, [room.id, room.version])

  useEffect(() => {
    if (selectedId) {
      dialog.current?.showModal()
      void api<PlayerNote[]>(`/api/rooms/${room.id}/game/players/${selectedId}/notes`)
        .then(setNotes).catch((cause) => setError(cause instanceof Error ? cause.message : 'Không tải được ghi chú.'))
    } else {
      dialog.current?.close()
      setNotes([])
      setError('')
    }
  }, [selectedId, room.id])

  function close() {
    dialog.current?.close()
    setSelectedId(null)
    setReason('')
    setEffect('')
    setNote('')
  }

  async function submit(action: Action, text?: string) {
    if (!selected || busy) return
    const actualReason = action === 'note' ? null : reason.trim()
    if (action !== 'note' && !actualReason) return
    if (['kill', 'revive'].includes(action)) {
      const label = action === 'kill' ? 'giết' : 'hồi sinh'
      if (!window.confirm(`Xác nhận ${label} ${selected.display_name}? Trạng thái kín chỉ quản trò thấy cho đến khi công bố.`)) return
    }
    setBusy(true)
    setError('')
    try {
      await api(`/api/rooms/${room.id}/game/players/${selected.user_id}/actions`, {
        method: 'POST', body: jsonBody({ client_request_id: crypto.randomUUID(), action,
          text: text ?? null, reason: actualReason }),
      })
      await refresh()
      if (action === 'note') {
        setNotes(await api<PlayerNote[]>(`/api/rooms/${room.id}/game/players/${selected.user_id}/notes`))
        setNote('')
      } else {
        setReason('')
        setEffect('')
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không thực hiện được thao tác.')
      await refresh().catch(() => {})
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

  const shown = players.filter((item) => filter === 'all' || (filter === 'alive' ? item.is_alive : !item.is_alive))
  return <section className="host-board" aria-labelledby="host-board-title">
    <span className="eyebrow">CHỈ QUẢN TRÒ</span><h2 id="host-board-title">Bảng người chơi</h2>
    <p>Trạng thái sống/chết, hiệu ứng và ghi chú ở đây là thông tin kín. Thao tác được ghi vào nhật ký trận.</p>
    <div className="board-filters" role="group" aria-label="Lọc trạng thái">
      {([['all', 'Tất cả'], ['alive', 'Đang sống'], ['dead', 'Đã chết']] as const).map(([value, label]) =>
        <button key={value} type="button" className={filter === value ? '' : 'button-secondary'}
          aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}
    </div>
    <ul className="board-list">{shown.map((item) => <li key={item.user_id} className="board-player">
      <div><small>GHẾ {item.seat_number.toString().padStart(2, '0')}</small><strong>{item.display_name}</strong>
        <span className={item.is_alive ? 'state-alive' : 'state-dead'}>{item.is_alive ? 'Đang sống' : 'Đã chết · chưa công bố'}</span></div>
      <div className="board-role"><RoleIcon role={item.role} roomId={room.id} size={32} />
        <span>{item.role.name}</span><span className={`faction-badge ${factionClass(item.role.faction_label)}`}>{item.role.faction_label}</span></div>
      <div className="board-summary">{item.effects.length ? `Hiệu ứng: ${item.effects.join(', ')}` : 'Không có hiệu ứng'} · {item.note_count} ghi chú</div>
      <button type="button" className="button-secondary" onClick={() => setSelectedId(item.user_id)}>Thao tác</button>
    </li>)}</ul>
    {!players.length && !error && <p>Đang tải người chơi…</p>}
    {error && !selectedId && <p className="form-message" role="alert">{error}</p>}

    <dialog ref={dialog} className="player-dialog" onClose={close}>
      {selected && <div className="player-dialog-content">
        <header><div><span className="eyebrow">GHẾ {selected.seat_number}</span>
          <h3>{selected.display_name}</h3><p>{selected.role.name} · {selected.role.faction_label} · {selected.is_alive ? 'Đang sống' : 'Đã chết'}</p></div>
          <button type="button" className="button-secondary" onClick={close} aria-label="Đóng">✕</button></header>
        {room.status === 'playing' ? <>
          <div className="stack-form player-action-block">
            <label htmlFor="player-action-reason">Lý do quyết định</label>
            <textarea id="player-action-reason" value={reason} maxLength={1000}
              onChange={(event) => setReason(event.target.value)} placeholder="Lý do sẽ xuất hiện trong nhật ký sau trận" />
            <div className="game-control-actions">
              <button type="button" disabled={busy || room.is_paused || !reason.trim() || !selected.is_alive}
                onClick={() => void submit('kill')}>Giết</button>
              <button type="button" className="button-secondary" disabled={busy || room.is_paused || !reason.trim() || selected.is_alive}
                onClick={() => void submit('revive')}>Hồi sinh</button>
            </div>
          </div>
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
          {notes.length ? <ol>{notes.map((item) => <li key={item.id}><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('vi-VN')}</time><p>{item.text}</p></li>)}</ol> : <p>Chưa có ghi chú.</p>}
        </div>
        {error && <p className="form-message" role="alert">{error}</p>}
      </div>}
    </dialog>
  </section>
}
