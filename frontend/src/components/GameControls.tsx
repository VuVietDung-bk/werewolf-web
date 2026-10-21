import { useEffect, useState, type FormEvent } from 'react'
import { api, jsonBody, type Room, type VoteRound } from '../api'
import { useRequestIds } from '../useRequestIds'
import { useConfirmation } from '../useConfirmation'

type Props = { room: Room; onRoomChanged: () => Promise<void> }

export default function GameControls({ room, onRoomChanged }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [pauseReason, setPauseReason] = useState('')
  const [resultLabel, setResultLabel] = useState('')
  const [resultReason, setResultReason] = useState('')
  const [openVoteDay, setOpenVoteDay] = useState<number | null>(null)
  const [voteWarning, setVoteWarning] = useState('')
  const requestIdFor = useRequestIds()
  const { ask, confirmationDialog } = useConfirmation()
  const phase = `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}`

  useEffect(() => {
    if (!room.is_host || room.status !== 'playing') return
    let active = true
    void api<VoteRound | null>(`/api/rooms/${room.id}/votes/latest`).then((latest) => {
      if (active) setOpenVoteDay(latest?.status === 'open' ? latest.day_number : null)
    }).catch(() => {})
    return () => { active = false }
  }, [room.id, room.version, room.status, room.is_host])

  async function voteClosedForTransition(): Promise<boolean> {
    try {
      const latest = await api<VoteRound | null>(`/api/rooms/${room.id}/votes/latest`)
      if (latest?.status === 'open') {
        setOpenVoteDay(latest.day_number)
        setVoteWarning('')
        return false
      }
      setOpenVoteDay(null)
      setVoteWarning('')
      return true
    } catch (cause) {
      setVoteWarning(cause instanceof Error ? cause.message : 'Không kiểm tra được vòng vote. Thử lại trước khi chuyển pha.')
      return false
    }
  }

  async function command(path: string, body?: object): Promise<boolean> {
    setBusy(true)
    setError('')
    const request = requestIdFor({ room_id: room.id, path, body: body ?? {} })
    try {
      await api<Room>(`/api/rooms/${room.id}/game/${path}`, {
        method: 'POST', body: jsonBody({ ...body, client_request_id: request.id }),
      })
      request.acknowledge()
      try {
        await onRoomChanged()
      } catch {
        setError('Lệnh đã được ghi nhưng chưa tải lại được phòng. Tải lại trang để xem trạng thái mới.')
      }
      return true
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không thực hiện được lệnh.')
      await onRoomChanged().catch(() => {})
      return false
    } finally { setBusy(false) }
  }

  async function pause(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !pauseReason.trim()) return
    const reason = pauseReason.trim()
    if (!await ask({ title: 'Tạm dừng trận', phase, subject: room.name,
      audience: 'Toàn bộ thành viên phòng', publicText: `Trận tạm dừng ở ${phase}.`,
      note: `Lý do quản trò lưu: ${reason}`, confirmLabel: 'Tạm dừng' })) return
    const ok = await command('pause', { reason })
    if (ok) setPauseReason('')
  }

  async function finish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !resultLabel.trim() || !resultReason.trim()) return
    if (!await voteClosedForTransition()) return
    const label = resultLabel.trim()
    const reason = resultReason.trim()
    if (!await ask({ title: 'Kết thúc trận', phase, subject: room.name,
      audience: 'Toàn bộ thành viên phòng', publicText: `${label}\n${reason}`,
      note: 'Sau khi kết thúc, quản trò không thể tiếp tục xử lý trận.',
      confirmLabel: 'Kết thúc và công bố' })) return
    await command('finish', { result_label: label, result_reason: reason })
  }

  if (!room.is_host || room.status !== 'playing') return null
  const next = room.phase_type === 'day' ? `Đêm ${room.round_number + 1}` : `Ngày ${room.round_number}`
  async function advance() {
    if (busy) return
    if (!await voteClosedForTransition()) return
    if (!await ask({ title: 'Chuyển pha', phase, subject: room.name,
      audience: 'Toàn bộ thành viên phòng', publicText: `Pha hiện tại kết thúc. Trận chuyển sang ${next}.`,
      note: 'Kiểm tra các quyết định và thông báo cần công bố trước khi tiếp tục.',
      confirmLabel: `Chuyển sang ${next}` })) return
    await command('advance', {
      expected_phase_type: room.phase_type, expected_round_number: room.round_number,
    })
  }
  async function resume() {
    if (busy) return
    if (!await ask({ title: 'Tiếp tục trận', phase, subject: room.name,
      audience: 'Toàn bộ thành viên phòng', publicText: `Trận tiếp tục ở ${phase}.`,
      confirmLabel: 'Tiếp tục trận' })) return
    await command('resume')
  }
  return <section className="game-controls" aria-labelledby="game-controls-title">
    <h3 id="game-controls-title">Điều khiển quản trò</h3>
    <p>Quản trò quyết định khi nào chuyển pha và kết quả trận. Mọi thay đổi được ghi vào nhật ký.</p>
    <div className="game-control-actions">
      <button type="button" disabled={busy || room.is_paused} onClick={() => void advance()}>Chuyển sang {next}</button>
      {room.is_paused && <button type="button" className="button-secondary" disabled={busy}
        onClick={() => void resume()}>Tiếp tục trận</button>}
    </div>
    {openVoteDay !== null && <p className="game-paused" role="status">Vòng vote Ngày {openVoteDay} còn mở. <a href="#vote">Đến tab Vote để đóng vòng</a> trước khi chuyển pha hoặc kết thúc trận.</p>}
    {voteWarning && <p className="form-message" role="alert">{voteWarning}</p>}
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
    {confirmationDialog}
  </section>
}
