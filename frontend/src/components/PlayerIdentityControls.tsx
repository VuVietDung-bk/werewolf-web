import { useState, type FormEvent } from 'react'
import { api, jsonBody, type PlayerBoardItem, type Role, type Room } from '../api'
import { useRequestIds } from '../useRequestIds'

export default function PlayerIdentityControls({ room, player, roles, onChanged }: {
  room: Room; player: PlayerBoardItem; roles: Role[]; onChanged: () => Promise<void>
}) {
  const [roleId, setRoleId] = useState(player.role.id)
  const [faction, setFaction] = useState(player.role.faction_label)
  const [team, setTeam] = useState(player.role.team_chat_label ?? '')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const requestIdFor = useRequestIds()

  async function change(action: 'change_role' | 'change_faction' | 'change_team', text?: string) {
    if (busy || !reason.trim()) return
    const label = action === 'change_role' ? 'role' : action === 'change_faction' ? 'phe' : 'đội chat'
    if (!window.confirm(`Xác nhận đổi ${label} của ${player.display_name}? Thay đổi được ghi vào nhật ký.`)) return
    setBusy(true)
    setError('')
    const payload = { action, role_id: action === 'change_role' ? roleId : null,
      text: action === 'change_role' ? null : text || null, reason: reason.trim() }
    const request = requestIdFor({ room_id: room.id, player_id: player.user_id, payload })
    let sent = false
    try {
      await api(`/api/rooms/${room.id}/game/players/${player.user_id}/actions`, {
        method: 'POST', body: jsonBody({ client_request_id: request.id, ...payload }),
      })
      sent = true
      request.acknowledge()
      setReason('')
      await onChanged()
    } catch (cause) {
      setError(sent ? 'Đã đổi thông tin nhưng chưa tải lại được bảng. Mở lại tab để kiểm tra.'
        : cause instanceof Error ? cause.message : 'Không đổi được thông tin người chơi.')
      if (!sent) await onChanged().catch(() => {})
    } finally { setBusy(false) }
  }

  function changeRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void change('change_role')
  }
  function changeFaction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (faction.trim()) void change('change_faction', faction.trim())
  }
  function changeTeam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void change('change_team', team.trim())
  }

  return <div className="identity-controls">
    <p>Đang dùng: <strong>{player.role.name}</strong> · phe <strong>{player.role.faction_label}</strong> · đội chat <strong>{player.role.team_chat_label ?? 'Không có'}</strong>.</p>
    <p>Đổi role sẽ lấy phe và đội chat mặc định của role mới. Có thể đổi riêng từng mục sau đó. Danh mục role của ván vẫn giữ nguyên.</p>
    {room.status === 'playing' && <>
      <label htmlFor="identity-reason">Lý do thay đổi</label>
      <textarea id="identity-reason" value={reason} maxLength={1000}
        onChange={(event) => setReason(event.target.value)} />
      <form className="stack-form player-action-block" onSubmit={changeRole}>
        <label htmlFor="new-role">Role mới</label>
        <select id="new-role" value={roleId} onChange={(event) => setRoleId(event.target.value)}>
          {roles.map((role) => <option key={role.id} value={role.id}>{role.name} · {role.faction_label}</option>)}
        </select>
        <button disabled={busy || room.is_paused || !reason.trim() || !roleId || roleId === player.role.id}>Đổi role</button>
      </form>
      <form className="stack-form player-action-block" onSubmit={changeFaction}>
        <label htmlFor="new-faction">Phe hiện tại</label>
        <input id="new-faction" value={faction} maxLength={80}
          onChange={(event) => setFaction(event.target.value)} />
        <button disabled={busy || room.is_paused || !reason.trim() || !faction.trim() || faction.trim() === player.role.faction_label}>Đổi phe</button>
      </form>
      <form className="stack-form player-action-block" onSubmit={changeTeam}>
        <label htmlFor="new-team">Đội chat hiện tại</label>
        <input id="new-team" value={team} maxLength={80}
          onChange={(event) => setTeam(event.target.value)} placeholder="Để trống để bỏ đội chat" />
        <button disabled={busy || room.is_paused || !reason.trim() || (team.trim() || null) === player.role.team_chat_label}>Đổi đội chat</button>
      </form>
    </>}
    {error && <p className="form-message" role="alert">{error}</p>}
  </div>
}
