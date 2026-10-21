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
  public_is_alive: boolean
  public_role_name: string | null
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
  draft_config: { candidate_role_ids: string[] } | null
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
  candidate_role_ids: string[] | null
  host_turns: DraftTurn[] | null
}

export type PlayerBoardItem = {
  user_id: string
  display_name: string
  seat_number: number
  role: Role
  is_alive: boolean
  public_is_alive: boolean
  publication_disposition: 'published' | 'pending' | 'held' | 'skipped'
  public_role_name: string | null
  effects: string[]
  note_count: number
  pending_request_count: number
}
export type PlayerNote = { id: string; text: string; created_at: string; edited_at: string | null }
export type VoteRound = {
  id: string
  day_number: number
  status: 'open' | 'closed'
  opened_at: string
  closed_at: string | null
  eligible: boolean
  my_choice: 'target' | 'skip' | 'withdrawn' | null
  my_target_user_id: string | null
  submitted_count: number | null
}
export type VoteHostRound = VoteRound & {
  decided_at: string | null
  published_at: string | null
}
export type VoteReview = {
  vote_round_id: string
  ballots: {
    voter_user_id: string
    display_name: string
    seat_number: number
    public_is_alive: boolean
    choice: 'target' | 'skip' | 'withdrawn' | null
    target_user_id: string | null
  }[]
  tally: { target_user_id: string; display_name: string; seat_number: number; count: number }[]
  skip_count: number
  withdrawn_count: number
  not_submitted_count: number
  decision: {
    decision_text: string
    reason: string
    target_user_id: string | null
    event_id: string
    decided_at: string
  } | null
  publication_event_id: string | null
  publication_deleted: boolean
}
export type PublicVoteNotice = {
  id: string
  vote_round_id: string
  day_number: number
  text: string
  created_at: string
  edited_at: string | null
}
export type DeathNotice = {
  id: string
  status: 'dead' | 'alive'
  user_id: string
  display_name: string
  seat_number: number
  public_cause: string | null
  revealed_role_name: string | null
  phase_type: 'day' | 'night'
  round_number: number
  created_at: string
  edited_at: string | null
}
export type PrivateMessage = {
  id: string
  event_id: string
  sender_user_id: string
  text: string
  created_at: string
  edited_at: string | null
}
export type ChatChannel = 'common' | 'team' | 'host'
export type ChatMessage = {
  id: string
  event_id: string
  sequence: number
  updated_sequence: number
  channel: ChatChannel
  team_label: string | null
  participant_user_id: string | null
  sender_user_id: string
  sender_name: string
  text: string
  created_at: string
  edited_at: string | null
  deleted: boolean
  history: { sequence: number; action: 'send' | 'edit' | 'delete'; text: string | null; created_at: string }[] | null
}
export type ChatPage = { messages: ChatMessage[]; next_before_sequence: number | null; next_after_sequence: number | null; next_after_updated_sequence: number | null; room_cursor: number }
export type ChatPolicy = {
  common_send_at_night: boolean
  team_send_in_day: boolean
  dead_common_send: boolean
  dead_team_send: boolean
}
export type ChatContext = {
  team_labels: string[]
  writable_team_labels: string[]
  current_team_label: string | null
  policy: ChatPolicy
  can_send_common: boolean
  can_send_team: boolean
  can_send_host: boolean
  is_muted: boolean
  mute_reason: string | null
  muted_players: { user_id: string; reason: string }[]
  activity: { channel: ChatChannel; team_label: string | null; participant_user_id: string | null; sequence: number }[]
}
export type RoleRequestContext = {
  can_submit: boolean
  reason: string | null
  role_name: string | null
  allowed_phases: ('day' | 'night')[]
  request_when_dead: boolean
  is_alive: boolean | null
}
export type RoleRequest = {
  id: string
  event_id: string
  sequence: number
  user_id: string
  sender_name: string
  role_id: string
  role_name: string
  phase_type: 'day' | 'night'
  round_number: number
  text: string
  status: string
  reply_text: string | null
  linked_event_ids: string[]
  updated_sequence: number
  created_at: string
  updated_at: string
}
export type RoleRequestPage = {
  requests: RoleRequest[]
  next_before_sequence: number | null
  next_after_sequence: number | null
  room_cursor: number
}
export type LinkableRoleAction = {
  event_id: string
  sequence: number
  kind: string
  target_name: string
  created_at: string
}
export type PublicCorrection = {
  id: string
  sequence: number
  source_event_id: string
  source_sequence: number
  source_kind: string
  text: string
  created_at: string
  edited_at: string | null
}
export type PublicAnnouncement = {
  id: string
  sequence: number
  phase_type: 'day' | 'night'
  round_number: number
  text: string
  kind: 'general' | 'death' | 'revival' | 'attack' | 'role' | 'vote' | 'other'
  related_player: { user_id: string; display_name: string; seat_number: number } | null
  source_player: { user_id: string; display_name: string; seat_number: number } | null
  created_at: string
  edited_at: string | null
}
export type LinkableAnnouncementEvent = {
  event_id: string
  sequence: number
  kind: string
  target_name: string | null
  created_at: string
}

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
