import { useEffect, useState } from 'react'
import type { Role } from '../api'
import { builtinIconUrl, roleIconUrl } from '../roleIcons'

export default function RoleIcon({ role, roomId, size = 48 }: {
  role: Pick<Role, 'name' | 'faction_label' | 'icon_key' | 'icon_id'>
  roomId: string
  size?: number
}) {
  const src = roleIconUrl(role, roomId)
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [src])
  return <img className="role-icon" src={failed ? builtinIconUrl('generic') : src}
    width={size} height={size} alt="" aria-hidden="true" onError={() => setFailed(true)} />
}
