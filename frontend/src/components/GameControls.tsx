import { useState, type FormEvent } from 'react'
import { api, jsonBody, type Room } from '../api'

type Props = { room: Room; onRoomChanged: () => Promise<void> }

export default function GameControls({ room, onRoomChanged }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pauseReason, setPauseReason] = useState('')
  const [resultLabel, setResultLabel] = useState('')
  const [resultReason, setResultReason] = useState('')

  async function command(path: string, body?: object): Promise<boolean> {
    setBusy(true)
    setError('')
    try {
      await api<Room>(`/api/rooms/${room.id}/game/${path}`, {
        method: 'POST', ...(body ? { body: jsonBody(body) } : {}),
      })
      await onRoomChanged()
      return true
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không thực hiện được lệnh.')
      await onRoomChanged().catch(() => {})
      return false
    } finally { setBusy(false) }
  }

  function pause(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!pauseReason.trim()) return
    void command('pause', { reason: pauseReason.trim() }).then((ok) => { if (ok) setPauseReason('') })
  }

  function finish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!resultLabel.trim() || !resultReason.trim()) return
    if (!window.confirm(`Kết thúc trận với kết quả “${resultLabel.trim()}”? Mọi thành viên sẽ thấy kết quả này.`)) return
    void command('finish', { result_label: resultLabel.trim(), result_reason: resultReason.trim() })
  }

  if (!room.is_host || room.status !== 'playing') return null
  const next = room.phase_type === 'day' ? `Đêm ${room.round_number + 1}` : `Ngày ${room.round_number}`
  return <section className="game-controls" aria-labelledby="game-controls-title">
    <h3 id="game-controls-title">Điều khiển quản trò</h3>
    <p>Quản trò quyết định khi nào chuyển pha và kết quả trận. Mọi thay đổi được ghi vào nhật ký.</p>
    <div className="game-control-actions">
      <button type="button" disabled={busy || room.is_paused} onClick={() => void command('advance', {
        expected_phase_type: room.phase_type, expected_round_number: room.round_number,
      })}>Chuyển sang {next}</button>
      {room.is_paused && <button type="button" className="button-secondary" disabled={busy}
        onClick={() => void command('resume')}>Tiếp tục trận</button>}
    </div>
    {!room.is_paused && <form className="stack-form game-control-form" onSubmit={pause}>
      <label htmlFor="pause-reason">Lý do tạm dừng</label>
      <input id="pause-reason" value={pauseReason} maxLength={1000} required
        onChange={(event) => setPauseReason(event.target.value)} />
      <button type="submit" className="button-secondary" disabled={busy || !pauseReason.trim()}>Tạm dừng</button>
    </form>}
    <form className="stack-form game-control-form" onSubmit={finish}>
      <h4>Kết thúc trận</h4>
      <label htmlFor="result-label">Bên thắng hoặc kết quả</label>
      <input id="result-label" value={resultLabel} maxLength={120} required
        onChange={(event) => setResultLabel(event.target.value)} placeholder="Ví dụ: Phe Làng thắng" />
      <label htmlFor="result-reason">Giải thích kết quả</label>
      <textarea id="result-reason" value={resultReason} maxLength={2000} required
        onChange={(event) => setResultReason(event.target.value)} />
      <button type="submit" className="button-secondary" disabled={busy || !resultLabel.trim() || !resultReason.trim()}>Kết thúc và công bố</button>
    </form>
    {error && <p className="form-message" role="alert">{error}</p>}
  </section>
}
