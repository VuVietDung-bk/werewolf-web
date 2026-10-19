import type { Role } from './api'

export type BuiltinIconKey = Exclude<Role['icon_key'], 'upload'>
export const ICON_CHOICES: { key: BuiltinIconKey; label: string }[] = [
  { key: 'auto', label: 'Tự chọn theo role/phe' },
  { key: 'generic', label: 'Role tự tạo' },
  { key: 'village', label: 'Làng' },
  { key: 'wolf', label: 'Sói' },
  { key: 'neutral', label: 'Trung lập' },
  { key: 'seer', label: 'Tiên Tri' },
  { key: 'hunter', label: 'Xạ thủ' },
  { key: 'potion', label: 'Phù thủy / hồi phục' },
]

function simplified(value: string): string {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/đ/g, 'd')
}

export function resolvedIconKey(role: Pick<Role, 'name' | 'faction_label' | 'icon_key'>): BuiltinIconKey {
  if (role.icon_key !== 'auto' && role.icon_key !== 'upload') return role.icon_key
  const name = simplified(role.name)
  const faction = simplified(role.faction_label).trim()
  if (name.includes('tien tri') || name.includes('seer')) return 'seer'
  if (name.includes('xa thu') || name.includes('hunter')) return 'hunter'
  if (name.includes('phu thuy') || name.includes('witch') || name.includes('healer')) return 'potion'
  if (['soi', 'ma soi', 'phe soi', 'wolf', 'wolves'].includes(faction)) return 'wolf'
  if (['lang', 'dan', 'dan lang', 'phe dan', 'phe lang', 'village'].includes(faction)) return 'village'
  if (['trung lap', 'phe trung lap', 'neutral'].includes(faction)) return 'neutral'
  return 'generic'
}

export function builtinIconUrl(key: BuiltinIconKey): string {
  return `/assets/v1/role-${key === 'auto' ? 'generic' : key}-v1-128.png`
}

export function roleIconUrl(role: Pick<Role, 'name' | 'faction_label' | 'icon_key' | 'icon_id'>,
                            roomId: string): string {
  if (role.icon_key === 'upload' && role.icon_id) return `/api/rooms/${roomId}/icons/${role.icon_id}`
  return builtinIconUrl(resolvedIconKey(role))
}
