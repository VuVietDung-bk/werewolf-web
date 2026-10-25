import { useEffect, useState } from 'react'
import type { Role } from '../api'
import { builtinIconUrl, roleIconUrl } from '../roleIcons'

export default function RoleIcon({ role, roomId, size = 48 }: {
  role: Pick<Role, 'name' | 'faction_label' | 'icon_key' | 'icon_id'>
  roomId: string
  size?: number
}) {
  const src = roleIconUrl(role, roomId)
  const generic = builtinIconUrl('generic')
  const [fallback, setFallback] = useState<'primary' | 'generic' | 'symbol'>('primary')
  useEffect(() => setFallback('primary'), [src])
  if (fallback === 'symbol') {
    return <span className="role-icon role-icon-fallback" style={{ width: size, height: size }}
      aria-hidden="true">✦</span>
  }
  return <img className="role-icon" src={fallback === 'generic' ? generic : src}
    width={size} height={size} alt="" aria-hidden="true"
    onError={() => setFallback(fallback === 'primary' && src !== generic ? 'generic' : 'symbol')} />
}
