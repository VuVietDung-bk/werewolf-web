import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, jsonBody, type DraftStatus, type RoleCatalog, type Room } from '../api'
import { factionClass } from '../factions'
import RoleIcon from './RoleIcon'

type DraftPreferences = { mode: 'random' | 'draft'; candidates: string[] | null }
const preferenceKey = (roomId: string) => `ma-soi-distribution-${roomId}`
function readPreferences(roomId: string): DraftPreferences {
  try {
    const value = JSON.parse(sessionStorage.getItem(preferenceKey(roomId)) ?? 'null') as Partial<DraftPreferences> | null
    return { mode: value?.mode === 'draft' ? 'draft' : 'random',
      candidates: Array.isArray(value?.candidates) && value.candidates.every((id) => typeof id === 'string')
        ? value.candidates : null }
  } catch { return { mode: 'random', candidates: null } }
}

export default function DraftPanel({ room, catalog, onRoomChanged, onRandomDistribution, randomBusy }: {
  room: Room; catalog: RoleCatalog; onRoomChanged: () => Promise<void>
  onRandomDistribution: () => Promise<void>; randomBusy: boolean
}) {
  const [mode, setMode] = useState<'random' | 'draft'>(() => readPreferences(room.id).mode)
  const [candidates, setCandidates] = useState<string[] | null>(() => readPreferences(room.id).candidates)
  const [status, setStatus] = useState<DraftStatus | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const statusRef = useRef<DraftStatus | null>(null)
  const changedRef = useRef(onRoomChanged)
  changedRef.current = onRoomChanged

  const factions = [...new Set(catalog.roles.map((role) => role.faction_label))]
  const selected = candidates ?? catalog.roles.map((role) => role.id)
  const total = catalog.roles.reduce((sum, role) => sum + role.quantity, 0)
  const needsMoreRoles = selected.length < room.members.length

  useEffect(() => {
    const valid = new Set(catalog.roles.map((role) => role.id))
    setCandidates((previous) => previous === null ? [...valid] : previous.filter((id) => valid.has(id)))
  }, [catalog.roles.map((role) => role.id).join('|')])

  useEffect(() => {
    try { sessionStorage.setItem(preferenceKey(room.id), JSON.stringify({ mode, candidates })) }
    catch { /* A private browser session can deny storage; the form still works. */ }
  }, [room.id, mode, candidates])

  useEffect(() => {
    if (catalog.distribution_mode !== 'draft') return
    let alive = true
    async function reload() {
      try {
        const next = await api<DraftStatus>(`/api/rooms/${room.id}/roles/draft/status`)
        if (!alive) return
        const previous = statusRef.current
        statusRef.current = next
        setStatus(next)
        if (previous && (previous.completed !== next.completed || previous.status !== next.status)) {
          void changedRef.current()
        }
      } catch (cause) {
        if (alive) setError(cause instanceof Error ? cause.message : 'Không tải được lượt chọn.')
      }
    }
    void reload()
    const timer = window.setInterval(() => { void reload() }, 8000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [catalog.distribution_mode, room.id, room.version])

  async function run(action: () => Promise<void>, success: string) {
    setBusy(true); setError(''); setNotice('')
    try {
      await action()
      setNotice(success)
      const next = await api<DraftStatus>(`/api/rooms/${room.id}/roles/draft/status`)
      statusRef.current = next
      setStatus(next)
      await onRoomChanged()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không thực hiện được.')
    } finally { setBusy(false) }
  }

  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void run(async () => {
      await api(`/api/rooms/${room.id}/roles/draft/start`, { method: 'POST', body: jsonBody({
        candidate_role_ids: selected,
      }) })
    }, 'Đã xáo lượt và mở lựa chọn đầu tiên.')
  }

  function toggleCandidate(id: string) {
    setCandidates((current) => {
      const existing = current ?? catalog.roles.map((role) => role.id)
      return existing.includes(id) ? existing.filter((value) => value !== id) : [...existing, id]
    })
  }

  const currentHostTurn = status?.host_turns?.find((turn) => turn.turn_index === status.current_turn)
  const byId = new Map(catalog.roles.map((role) => [role.id, role]))
  return <section className="draft-panel" aria-labelledby="draft-title">
    <div className="role-heading"><div><span className="eyebrow">TRƯỚC KHI BẮT ĐẦU VÁN</span><h3 id="draft-title">Cách phát role</h3></div>
      {catalog.distribution_mode === 'draft' && <button type="button" className="button-secondary" disabled={busy}
        onClick={() => void run(async () => {}, 'Đã cập nhật lượt chọn.')}>Cập nhật lượt</button>}
    </div>
    {room.is_host && !catalog.locked && <>
      <div className="distribution-modes" role="group" aria-label="Chọn cách phát role">
        <button type="button" className={mode === 'random' ? 'distribution-mode selected' : 'distribution-mode'}
          aria-pressed={mode === 'random'} onClick={() => setMode('random')}>
          <strong>Phát ngẫu nhiên</strong><span>Phát kín theo số bản của từng role.</span></button>
        <button type="button" className={mode === 'draft' ? 'distribution-mode selected' : 'distribution-mode'}
          aria-pressed={mode === 'draft'} onClick={() => setMode('draft')}>
          <strong>Chọn tối đa 3 role</strong><span>Xáo thứ tự; role đã chọn rời kho. Mỗi lượt chọn một role hoặc Random.</span></button>
      </div>
      {mode === 'random' ? <div className="distribution-details">
        <p><strong>{total}/{room.members.length}</strong> bản role. Chỉnh “Số bản” trên thẻ role để tổng bằng số người chơi.</p>
        <button type="button" disabled={randomBusy || room.status !== 'closed' || total !== room.members.length || total < 2}
          onClick={() => void onRandomDistribution()}>Phát role ngẫu nhiên</button>
      </div> : <form className="draft-config stack-form distribution-details" onSubmit={start}>
      <p>Chọn role có thể xuất hiện. Mỗi lượt hệ thống mời tối đa ba role từ kho còn lại; role được chọn sẽ rời kho. Không giữ số ghế cố định theo phe. Số bản trên thẻ role chỉ áp dụng cho cách phát ngẫu nhiên.</p>
      <div className="draft-factions">{factions.map((faction) => <fieldset key={faction}>
        <legend><span className={`faction-badge ${factionClass(faction)}`}>{faction}</span></legend>
        <div className="draft-candidates">{catalog.roles.filter((role) => role.faction_label === faction).map((role) =>
          <label key={role.id} className="consent"><input type="checkbox" checked={selected.includes(role.id)}
            onChange={() => toggleCandidate(role.id)} /><span>{role.name}</span></label>)}</div>
      </fieldset>)}</div>
      <p><strong>{selected.length}</strong> role ứng viên · <strong>{room.members.length}</strong> người chơi. Khi chọn Random, hệ thống rút từ toàn bộ role ứng viên còn hợp lệ, có thể khác ba role đang mời.</p>
      <p>Các lượt cuối sẽ thấy một hoặc hai lựa chọn nếu kho còn ít role.</p>
      {needsMoreRoles && <p className="form-message">Cần ít nhất {room.members.length} role ứng viên để mỗi người nhận một role riêng.</p>}
      <button disabled={busy || room.status !== 'closed' || room.members.length < 2 || selected.length === 0 || needsMoreRoles}>Xáo lượt và bắt đầu chọn role</button>
      {room.status === 'open' && <small>Đóng đăng ký trước khi bắt đầu.</small>}
    </form>}</>}
    {catalog.distribution_mode === 'draft' && status && <div className="draft-progress">
      <p><strong>{status.completed}/{status.total}</strong> người đã chọn · {status.current_turn ?
        `đang ở lượt ${status.current_turn}` : 'đã hoàn tất'}</p>
      {status.my_turn && <p>Lượt của bạn: <strong>{status.my_turn}/{status.total}</strong>.</p>}
      {status.my_offers && <div className="draft-offers"><h4>{status.my_offers.length} role để chọn</h4><div className="role-list">
        {status.my_offers.map((role) => <article className={`role-card ${factionClass(role.faction_label)}`} key={role.id}>
          <div className="role-card-heading"><div className="role-title"><RoleIcon role={role} roomId={room.id} /><strong>{role.name}</strong></div><span className={`faction-badge ${factionClass(role.faction_label)}`}>{role.faction_label}</span></div>
          <p>{role.description}</p><small>Tiên tri soi ra: {role.seer_result}</small>
          <button type="button" disabled={busy} onClick={() => void run(async () => {
            await api(`/api/rooms/${room.id}/roles/draft/choose`, { method: 'POST', body: jsonBody({ role_id: role.id }) })
          }, `Bạn đã chọn ${role.name}.`)}>Chọn role này</button>
        </article>)}</div>
        <button type="button" className="button-secondary" disabled={busy} onClick={() => void run(async () => {
          await api(`/api/rooms/${room.id}/roles/draft/choose`, { method: 'POST', body: jsonBody({ choose_random: true }) })
        }, 'Hệ thống đã chọn ngẫu nhiên từ kho role hợp lệ cho bạn.')}>Random từ kho role hợp lệ</button>
      </div>}
      {!room.is_host && status.current_turn && !status.my_offers && <p>Đang chờ đến lượt. Bạn có thể xem danh mục role trong lúc chờ.</p>}
      {room.is_host && status.host_turns && <div className="draft-host-status">
        <h4>Thứ tự và tiến độ</h4><ol>{status.host_turns.map((turn) => <li key={turn.user_id}>
          {room.members.find((member) => member.user_id === turn.user_id)?.display_name ?? turn.user_id}
          {' · '}{turn.chosen_role_id ? `đã chọn ${byId.get(turn.chosen_role_id)?.name ?? ''}` :
            turn.turn_index === status.current_turn ? 'đang chọn' : 'đang chờ'}
        </li>)}</ol>
        {currentHostTurn && <div className="draft-host-choice"><p>Lượt hiện tại: <strong>{room.members.find((member) => member.user_id === currentHostTurn.user_id)?.display_name}</strong></p>
          <p>Role đang mời: {currentHostTurn.offers?.map((id) => byId.get(id)?.name).join(' · ')}</p>
          <label htmlFor="draft-host-reason">Lý do chọn hộ nếu người chơi vắng mặt</label>
          <textarea id="draft-host-reason" value={reason} maxLength={1000} rows={2}
            onChange={(event) => setReason(event.target.value)} />
          <button type="button" disabled={busy || !reason.trim()} onClick={() => void run(async () => {
            await api(`/api/rooms/${room.id}/roles/draft/host-random`, { method: 'POST', body: jsonBody({ reason: reason.trim() }) })
            setReason('')
          }, 'Đã chọn ngẫu nhiên hộ người chơi.')}>Chọn ngẫu nhiên hộ</button>
        </div>}
      </div>}
    </div>}
    {error && <p className="form-message" role="alert">{error}</p>}
    {notice && <p className="role-notice" role="status">{notice}</p>}
  </section>
}
