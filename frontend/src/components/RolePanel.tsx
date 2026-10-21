import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, jsonBody, type Role, type RoleAssignment, type RoleCatalog, type Room } from '../api'
import { factionClass } from '../factions'
import { ICON_CHOICES, builtinIconUrl, roleIconUrl } from '../roleIcons'
import DraftPanel from './DraftPanel'
import RoleIcon from './RoleIcon'

type Draft = Omit<Role, 'id'>
const blank: Draft = {
  name: '', description: '', faction_label: 'Làng', seer_result: '', team_chat_label: null, quantity: 0,
  role_kind: 'passive', request_phases: [], request_when_dead: false,
  host_contact: { can_initiate: false, initiate_phases: [], reply_allowed: true, when_dead: false },
  icon_key: 'auto', icon_id: null,
}
const phaseName = (phase: string) => phase === 'day' ? 'ngày' : 'đêm'

export default function RolePanel({ room, view, onRoomChanged }: {
  room: Room; view: 'catalog' | 'manage' | 'distribution' | 'assignment'; onRoomChanged: () => Promise<void>
}) {
  const [catalog, setCatalog] = useState<RoleCatalog | null>(null)
  const [assignment, setAssignment] = useState<RoleAssignment | null>(null)
  const [allAssignments, setAllAssignments] = useState<RoleAssignment[]>([])
  const [draft, setDraft] = useState<Draft>(blank)
  const [quantityDrafts, setQuantityDrafts] = useState<Record<string, string>>({})
  const [editingId, setEditingId] = useState<string | null>(null)
  const [setName, setSetName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const dialogRef = useRef<HTMLDialogElement>(null)

  async function refresh() {
    const next = await api<RoleCatalog>(`/api/rooms/${room.id}/role-catalog`)
    setCatalog(next)
    setSetName(next.name)
    if (room.is_host) {
      setAllAssignments(await api<RoleAssignment[]>(`/api/rooms/${room.id}/roles/assignments`))
    } else {
      setAssignment(await api<RoleAssignment | null>(`/api/rooms/${room.id}/roles/my-assignment`))
    }
  }

  useEffect(() => {
    let alive = true
    setCatalog(null)
    setAssignment(null)
    setAllAssignments([])
    void api<RoleCatalog>(`/api/rooms/${room.id}/role-catalog`).then(async (next) => {
      if (!alive) return
      setCatalog(next)
      setSetName(next.name)
      const path = room.is_host ? 'assignments' : 'my-assignment'
      const result = await api<RoleAssignment[] | RoleAssignment | null>(`/api/rooms/${room.id}/roles/${path}`)
      if (!alive) return
      if (room.is_host) setAllAssignments(result as RoleAssignment[])
      else setAssignment(result as RoleAssignment | null)
    }).catch((cause) => { if (alive) setError(cause instanceof Error ? cause.message : 'Không tải được role.') })
    return () => { alive = false }
  }, [room.id, room.version, room.is_host])

  useEffect(() => {
    if (room.is_host || !['playing', 'finished'].includes(room.status)) return
    let active = true
    const timer = window.setInterval(() => {
      void api<RoleAssignment | null>(`/api/rooms/${room.id}/roles/my-assignment`)
        .then((latest) => { if (active) setAssignment(latest) })
        .catch(() => {})
    }, 8000)
    return () => { active = false; window.clearInterval(timer) }
  }, [room.id, room.status, room.is_host])

  async function run(action: () => Promise<void>, success: string): Promise<boolean> {
    setBusy(true); setError(''); setNotice('')
    try {
      await action()
      setNotice(success)
      try { await refresh(); await onRoomChanged() }
      catch { setError('Đã lưu, nhưng chưa tải lại được dữ liệu. Hãy bấm “Tải lại role”.') }
      return true
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không thực hiện được.')
      return false
    }
    finally { setBusy(false) }
  }

  function saveRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void (async () => {
      const wasEditing = editingId !== null
      const saved = await run(async () => {
        const path = editingId ? `/api/rooms/${room.id}/roles/${editingId}` : `/api/rooms/${room.id}/roles`
        await api<Role>(path, { method: editingId ? 'PUT' : 'POST', body: jsonBody(draft) })
      }, wasEditing ? 'Đã cập nhật role.' : 'Đã thêm role.')
      if (saved) dialogRef.current?.close()
    })()
  }

  function startCreate() {
    setEditingId(null)
    setDraft(blank)
    setError('')
    dialogRef.current?.showModal()
  }

  function startEdit(role: Role) {
    setEditingId(role.id)
    setDraft({ name: role.name, description: role.description, faction_label: role.faction_label,
      seer_result: role.seer_result, team_chat_label: role.team_chat_label,
      quantity: role.quantity, role_kind: role.role_kind,
      request_phases: [...role.request_phases], request_when_dead: role.request_when_dead,
      icon_key: role.icon_key, icon_id: role.icon_id,
      host_contact: { ...role.host_contact, initiate_phases: [...role.host_contact.initiate_phases] } })
    setError('')
    dialogRef.current?.showModal()
  }

  function saveQuantity(event: FormEvent<HTMLFormElement>, role: Role) {
    event.preventDefault()
    const value = quantityDrafts[role.id] ?? String(role.quantity)
    const quantity = Number(value)
    if (!value || !Number.isInteger(quantity) || quantity < 0 || quantity > 30) return
    void (async () => {
      const saved = await run(async () => {
        await api<Role>(`/api/rooms/${room.id}/roles/${role.id}/quantity`, {
          method: 'PATCH', body: jsonBody({ quantity }),
        })
      }, `Đã lưu số bản của ${role.name}.`)
      if (saved) setQuantityDrafts((current) => {
        const next = { ...current }
        delete next[role.id]
        return next
      })
    })()
  }

  function togglePhase(field: 'request_phases' | 'initiate_phases', phase: 'day' | 'night') {
    if (field === 'request_phases') setDraft((item) => ({ ...item,
      request_phases: item.request_phases.includes(phase)
        ? item.request_phases.filter((value) => value !== phase) : [...item.request_phases, phase] }))
    else setDraft((item) => ({ ...item, host_contact: { ...item.host_contact,
      initiate_phases: item.host_contact.initiate_phases.includes(phase)
        ? item.host_contact.initiate_phases.filter((value) => value !== phase)
        : [...item.host_contact.initiate_phases, phase] } }))
  }

  async function uploadIcon(file?: File) {
    if (!file) return
    setError('')
    if (!['image/png', 'image/webp', 'image/jpeg'].includes(file.type) || file.size > 1024 * 1024) {
      setError('Chọn PNG, WebP hoặc JPEG tĩnh tối đa 1 MiB.')
      return
    }
    setBusy(true)
    try {
      const uploaded = await api<{ id: string }>(`/api/rooms/${room.id}/icons`, {
        method: 'POST', body: file, headers: { 'Content-Type': file.type },
      })
      setDraft((current) => ({ ...current, icon_key: 'upload', icon_id: uploaded.id }))
      setNotice('Đã tải icon. Bấm “Lưu role” để dùng icon này.')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không tải được icon.')
    } finally { setBusy(false) }
  }

  const total = catalog?.roles.reduce((count, role) => count + role.quantity, 0) ?? 0
  const canEdit = room.is_host && catalog !== null && !catalog.locked && ['open', 'closed'].includes(room.status)
  const editable = view === 'manage' && canEdit
  return <section className={`role-panel role-panel-${view}`} aria-label={view === 'catalog' ? 'Danh mục role' : view === 'manage' ? 'Thiết lập role' : view === 'distribution' ? 'Phát role' : 'Role đã phân'}>
    {(view === 'catalog' || view === 'manage') && <>
    <div className="role-heading">
      <div><span className="eyebrow">{view === 'manage' ? 'THIẾT LẬP ROLE' : 'DANH MỤC CÔNG KHAI'}</span><h3 id="role-panel-title">{catalog?.name ?? 'Đang tải bộ role…'}</h3></div>
      <div className="role-heading-actions">
        {catalog && <span className="role-total">{catalog.distribution_mode === 'draft' ? 'Chọn tối đa 3 role' : `Tổng số bản: ${total}/${room.members.length}`}</span>}
        {editable && <button type="button" onClick={startCreate} disabled={busy}>+ Thêm role</button>}
        <button type="button" className="button-secondary" onClick={() => void refresh()} disabled={busy}>Tải lại role</button>
      </div>
    </div>
    <p>Mọi thành viên thấy mô tả đầy đủ của các role. Ai giữ role nào chỉ hiện riêng cho người đó và quản trò.</p>
    {catalog?.locked && <p className="role-notice">Bộ role đã khóa cho ván này · {catalog.distribution_mode === 'draft' ? 'chọn tối đa 3 role' : 'phát ngẫu nhiên'}.</p>}
    {catalog && <div className="role-list">{catalog.roles.length ? catalog.roles.map((role) =>
      <article className={`role-card ${factionClass(role.faction_label)}`} key={role.id}>
        <div className="role-card-heading"><div className="role-title"><RoleIcon role={role} roomId={room.id} /><strong>{role.name}</strong></div><span className={`faction-badge ${factionClass(role.faction_label)}`}>{role.faction_label}</span></div>
        <p>{role.description}</p>
        <small>Loại: {role.role_kind === 'active' ? 'Chủ động' : 'Bị động'} · Tiên tri soi ra: <strong>{role.seer_result}</strong></small>
        <small>Đội chat: {role.team_chat_label || 'không có'}</small>
        {editable ? <form className="role-quantity" onSubmit={(event) => saveQuantity(event, role)}>
          <label htmlFor={`role-quantity-${role.id}`}>Số bản</label>
          <input id={`role-quantity-${role.id}`} type="number" inputMode="numeric" min={0} max={30} required
            value={quantityDrafts[role.id] ?? String(role.quantity)} disabled={busy}
            onChange={(event) => setQuantityDrafts((current) => ({ ...current, [role.id]: event.target.value }))} />
          <button type="submit" disabled={busy || quantityDrafts[role.id] === undefined ||
            quantityDrafts[role.id] === '' || !Number.isInteger(Number(quantityDrafts[role.id])) ||
            Number(quantityDrafts[role.id]) < 0 || Number(quantityDrafts[role.id]) > 30 ||
            Number(quantityDrafts[role.id]) === role.quantity}>Lưu số bản</button>
        </form> : catalog.distribution_mode === 'draft' ?
          <small>{catalog.draft_config?.candidate_role_ids.includes(role.id) ? 'Nằm trong kho chọn role' : 'Không nằm trong bộ ứng viên'}</small> :
          <small>Số bản: {role.quantity}</small>}
        <small>Yêu cầu: {role.request_phases.length ? role.request_phases.map(phaseName).join(', ') : 'không chủ động'}{role.request_when_dead ? ' · được gửi khi chết' : ''}</small>
        <small>Chat quản trò: {role.host_contact.can_initiate ? `mở lúc ${role.host_contact.initiate_phases.map(phaseName).join(', ') || 'không có pha'}` : 'không tự mở'}{role.host_contact.reply_allowed ? ' · được trả lời khi quản trò mở' : ''}{role.host_contact.when_dead ? ' · được liên hệ khi chết' : ''}</small>
        {editable && <div className="role-card-actions"><button type="button" className="button-secondary" onClick={() => startEdit(role)} disabled={busy}>Sửa</button>
          <button type="button" className="button-secondary" disabled={busy} onClick={() => {
            if (window.confirm(`Xóa role ${role.name}?`)) void run(async () => {
              await api<void>(`/api/rooms/${room.id}/roles/${role.id}`, { method: 'DELETE' })
            }, 'Đã xóa role.')
          }}>Xóa</button></div>}
      </article>) : <p>Chưa có role nào. Quản trò có thể thêm role hoặc nạp bộ mẫu.</p>}</div>}

    </>}

    {view === 'assignment' && <>
    <div className="role-heading"><div><span className="eyebrow">PHÂN ROLE KÍN</span><h2>{room.is_host ? 'Phân role của quản trò' : 'Role của bạn'}</h2></div></div>
    {assignment && <aside className={`my-role ${factionClass(assignment.role.faction_label)}`}><span className="eyebrow">ROLE CỦA BẠN</span><div className="role-title"><RoleIcon role={assignment.role} roomId={room.id} size={64} /><h3>{assignment.role.name}</h3></div>
      <p>{assignment.role.description}</p><span className={`faction-badge ${factionClass(assignment.role.faction_label)}`}>{assignment.role.faction_label}</span>
      <small>Tiên tri soi ra: {assignment.role.seer_result}</small></aside>}
    {room.is_host && allAssignments.length > 0 && <aside className="host-assignments"><h3>Người chơi và role</h3><ul>
      {allAssignments.map((item) => <li key={item.user_id}><RoleIcon role={item.role} roomId={room.id} size={32} />{room.members.find((member) => member.user_id === item.user_id)?.display_name ?? item.user_id}: <strong>{item.role.name}</strong> <span className={`faction-badge ${factionClass(item.role.faction_label)}`}>{item.role.faction_label}</span></li>)}
    </ul></aside>}
    {!room.is_host && !assignment && <p>Đang chờ role được phát cho bạn.</p>}
    </>}

    {view === 'manage' && editable && <div className="role-editor">
      <form className="stack-form" onSubmit={(event) => { event.preventDefault(); void run(async () => {
        await api(`/api/rooms/${room.id}/role-set`, { method: 'PATCH', body: jsonBody({ name: setName }) })
      }, 'Đã đổi tên bộ role.') }}><label htmlFor="role-set-name">Tên bộ role</label>
        <input id="role-set-name" value={setName} maxLength={120} required onChange={(event) => setSetName(event.target.value)} />
        <button disabled={busy || !setName.trim()}>Lưu tên bộ role</button></form>
      {catalog?.roles.length === 0 && <button type="button" className="button-secondary" disabled={busy}
        onClick={() => void run(async () => { await api(`/api/rooms/${room.id}/role-set/sample`, { method: 'POST' }) }, 'Đã nạp bốn role mẫu; hãy điều chỉnh số bản.')}>Nạp bộ role mẫu</button>}
    </div>}
    {view === 'distribution' && catalog && (canEdit || catalog.distribution_mode === 'draft') &&
      <DraftPanel room={room} catalog={catalog} onRoomChanged={onRoomChanged} randomBusy={busy}
        onRandomDistribution={async () => { await run(async () => {
          await api(`/api/rooms/${room.id}/roles/distribute-random`, { method: 'POST' })
        }, 'Đã phát role cho mọi người.') }} />}
    {view === 'manage' && editable && <dialog ref={dialogRef} className="role-dialog" aria-labelledby="role-dialog-title"
      onCancel={(event) => { if (busy) event.preventDefault() }}
      onClose={() => { setEditingId(null); setDraft(blank); setError('') }}>
      <form className="role-modal-form" onSubmit={saveRole}>
        <div className="role-modal-head"><div><span className="eyebrow">THIẾT LẬP ROLE</span><h3 id="role-dialog-title">{editingId ? `Sửa ${draft.name}` : 'Thêm role mới'}</h3></div>
          <button type="button" className="button-secondary" aria-label="Đóng hộp thoại" disabled={busy} onClick={() => dialogRef.current?.close()}>✕</button></div>
        <div className="role-modal-body stack-form">
          <label htmlFor="role-name">Tên role</label><input id="role-name" autoFocus value={draft.name} maxLength={80} required onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
          <label htmlFor="role-description">Mô tả đầy đủ điều kiện, giới hạn và cách dùng</label>
          <textarea id="role-description" value={draft.description} maxLength={4000} required rows={5} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
          <fieldset className="role-icon-picker"><legend>Icon role</legend>
            <div className="role-icon-choices">{ICON_CHOICES.map((choice) => <label key={choice.key} className={draft.icon_key === choice.key ? 'role-icon-choice selected' : 'role-icon-choice'}>
              <input type="radio" name="role-icon" checked={draft.icon_key === choice.key}
                onChange={() => setDraft((current) => ({ ...current, icon_key: choice.key, icon_id: null }))} />
              <img src={choice.key === 'auto' ? roleIconUrl({ ...draft, icon_key: 'auto' }, room.id) : builtinIconUrl(choice.key)} alt="" width={40} height={40} />
              <span>{choice.label}</span>
            </label>)}</div>
            <label className="role-icon-upload">Tải icon từ máy (PNG/WebP/JPEG tĩnh, tối đa 1 MiB và 512 × 512 px)
              <input type="file" accept="image/png,image/webp,image/jpeg" disabled={busy}
                onChange={(event) => { void uploadIcon(event.target.files?.[0]); event.target.value = '' }} />
            </label>
            {draft.icon_key === 'upload' && draft.icon_id && <div className="role-icon-uploaded">
              <img src={roleIconUrl(draft, room.id)} alt="Icon đã tải lên" width={48} height={48} />
              <span>Đang dùng icon đã tải lên</span></div>}
          </fieldset>
        <div className="role-form-grid"><label>Phe<input value={draft.faction_label} maxLength={80} required onChange={(event) => setDraft({ ...draft, faction_label: event.target.value })} /></label>
          <label>Tiên tri soi ra<input value={draft.seer_result} list="seer-result-options" maxLength={80} required placeholder="Thiện, Ác, Không rõ…" onChange={(event) => setDraft({ ...draft, seer_result: event.target.value })} /></label>
          <datalist id="seer-result-options"><option value="Thiện" /><option value="Ác" /><option value="Không rõ" /></datalist>
          <label>Đội chat (tùy chọn)<input value={draft.team_chat_label ?? ''} maxLength={80} onChange={(event) => setDraft({ ...draft, team_chat_label: event.target.value || null })} /></label>
          <label>Loại role<select value={draft.role_kind} onChange={(event) => { const kind = event.target.value as Draft['role_kind']; setDraft({ ...draft, role_kind: kind,
            request_phases: kind === 'passive' ? [] : draft.request_phases,
            request_when_dead: kind === 'passive' ? false : draft.request_when_dead,
            host_contact: kind === 'passive' ? { ...draft.host_contact, can_initiate: false, initiate_phases: [] } : draft.host_contact }) }}>
            <option value="passive">Bị động</option><option value="active">Chủ động</option></select></label></div>
        {draft.role_kind === 'active' && <>
          <fieldset><legend>Pha được gửi yêu cầu tự do</legend>{(['day', 'night'] as const).map((phase) =>
            <label key={phase} className="consent"><input type="checkbox" checked={draft.request_phases.includes(phase)} onChange={() => togglePhase('request_phases', phase)} />{phaseName(phase)}</label>)}</fieldset>
          <label className="consent"><input type="checkbox" checked={draft.request_when_dead} onChange={(event) => setDraft({ ...draft, request_when_dead: event.target.checked })} />Được gửi yêu cầu khi đã chết</label>
          <label className="consent"><input type="checkbox" checked={draft.host_contact.can_initiate} onChange={(event) => setDraft({ ...draft, host_contact: { ...draft.host_contact, can_initiate: event.target.checked, initiate_phases: event.target.checked ? draft.host_contact.initiate_phases.length ? draft.host_contact.initiate_phases : ['day', 'night'] : [] } })} />Được tự mở chat quản trò</label>
          {draft.host_contact.can_initiate && <fieldset><legend>Pha được mở chat quản trò</legend>{(['day', 'night'] as const).map((phase) =>
            <label key={phase} className="consent"><input type="checkbox" checked={draft.host_contact.initiate_phases.includes(phase)} onChange={() => togglePhase('initiate_phases', phase)} />{phaseName(phase)}</label>)}</fieldset>}
        </>}
        <label className="consent"><input type="checkbox" checked={draft.host_contact.reply_allowed} onChange={(event) => setDraft({ ...draft, host_contact: { ...draft.host_contact, reply_allowed: event.target.checked } })} />Được trả lời khi quản trò mở chat trước</label>
        <label className="consent"><input type="checkbox" checked={draft.host_contact.when_dead} onChange={(event) => setDraft({ ...draft, host_contact: { ...draft.host_contact, when_dead: event.target.checked } })} />Được liên hệ quản trò khi đã chết</label>
          {error && <p className="form-message" role="alert">{error}</p>}
        </div>
        <div className="role-modal-footer"><button type="button" className="button-secondary" disabled={busy} onClick={() => dialogRef.current?.close()}>Hủy</button>
          <button type="submit" disabled={busy || !draft.name.trim() || !draft.description.trim() || !draft.seer_result.trim()}>Lưu role</button></div>
      </form>
    </dialog>}
    {error && !dialogRef.current?.open && <p className="form-message" role="alert">{error}</p>}
    {notice && <p className="role-notice" role="status">{notice}</p>}
    {(view === 'catalog' || view === 'manage') && <small className="role-help">Role và mô tả chỉ là dữ liệu; quản trò tự xác nhận và áp dụng hiệu ứng trong ván.</small>}
  </section>
}
