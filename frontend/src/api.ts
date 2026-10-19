export type Identity = { id: string; display_name: string }
export type RoomSummary = {
  id: string
  code: string
  name: string
  seat_limit: number
  status: string
  phase_type: string | null
  round_number: number
  is_paused: boolean
  result_label: string | null
  result_reason: string | null
  ended_at: string | null
  is_host: boolean
}
export type RoomMember = {
  user_id: string
  display_name: string
  seat_number: number
  joined_at: string
}
export type Room = RoomSummary & {
  host_id: string
  host_name: string
  version: number
  members: RoomMember[]
}

export type Role = {
  id: string
  name: string
  description: string
  faction_label: string
  seer_result: string
  team_chat_label: string | null
  quantity: number
  role_kind: 'active' | 'passive'
  request_phases: ('day' | 'night')[]
  request_when_dead: boolean
  host_contact: {
    can_initiate: boolean
    initiate_phases: ('day' | 'night')[]
    reply_allowed: boolean
    when_dead: boolean
  }
  icon_key: 'auto' | 'generic' | 'wolf' | 'village' | 'neutral' | 'seer' | 'hunter' | 'potion' | 'upload'
  icon_id: string | null
}
export type RoleCatalog = {
  name: string
  locked: boolean
  distribution_mode: string | null
  roles: Role[]
  draft_config: { candidate_role_ids: string[];
    allow_duplicate_roles: boolean } | null
}
export type RoleAssignment = { user_id: string; role: Role }
export type DraftTurn = {
  turn_index: number
  user_id: string
  offers: string[] | null
  chosen_role_id: string | null
  chosen_by_user_id: string | null
  choice_mode: string | null
  choice_reason: string | null
}
export type DraftStatus = {
  mode: string | null
  status: string
  total: number
  completed: number
  current_turn: number | null
  my_turn: number | null
  my_offers: Role[] | null
  faction_seats: Record<string, number> | null
  candidate_role_ids: string[] | null
  allow_duplicate_roles: boolean | null
  host_turns: DraftTurn[] | null
}

export type PlayerBoardItem = {
  user_id: string
  display_name: string
  seat_number: number
  role: Role
  is_alive: boolean
  effects: string[]
  note_count: number
}
export type PlayerNote = { id: string; text: string; created_at: string }

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers)
  if (typeof options?.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(path, {
    ...options,
    headers,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = typeof body?.detail === 'string' ? body.detail : 'Yêu cầu không thực hiện được.'
    throw new Error(detail)
  }
  return response.status === 204 ? undefined as T : await response.json() as T
}

export function jsonBody(value: unknown): string {
  return JSON.stringify(value)
}
