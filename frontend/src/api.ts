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
  next_room_id: string | null
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

export type TranscriptInfo = {
  room_id: string
  room_name: string
  result_label: string | null
  result_reason: string | null
  ended_at: string | null
  members: Record<string, string>
  roles: Record<string, string>
  limitation: string
}
export type TranscriptEvent = {
  id: string
  sequence: number
  created_at: string
  actor_user_id: string | null
  client_request_id: string | null
  actor_name: string | null
  kind: string
  visibility: string
  phase_type: string | null
  round_number: number | null
  subject_user_id: string | null
  subject_name: string | null
  reason: string | null
  before: unknown
  after: unknown
  related_event_ids: string[]
  references: Record<string, string>
  payload: Record<string, unknown>
}
export type TranscriptPage = {
  info: TranscriptInfo
  events: TranscriptEvent[]
  next_after_sequence: number | null
  total: number
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
  publication_disposition: 'published' | 'pending' | 'held' | 'skipped' | 'scheduled'
  public_role_name: string | null
  effects: string[]
  note_count: number
  pending_request_count: number
}
export type PlayerNote = { id: string; text: string; created_at: string; edited_at: string | null }
export type ScheduledAnnouncement = {
  id: string
  kind: 'status' | 'custom'
  player_id: string | null
  text: string
  target_phase_type: 'day' | 'night'
  target_round_number: number
  created_at: string
}
export type VoteRound = {
  id: string
  day_number: number
  status: 'open' | 'closed'
  opened_at: string
  closed_at: string | null
  closes_at: string | null
  server_now: string
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
  sequence: number
  sender_user_id: string
  text: string
  created_at: string
  edited_at: string | null
}
export type ChatChannel = 'common' | 'team' | 'host'
export type ReactionSummary = { emoji:string;count:number;mine:boolean }
export type ChatMessage = {
  source_kind: 'chat' | 'private_message' | 'role_request'
  id: string
  sender_user_id: string
  sender_name: string
  text: string
  created_at: string
  phase_type?: 'day' | 'night' | null
  round_number?: number | null
  edited_at: string | null
  deleted: boolean
  actions: { edit: boolean; delete: boolean; react: boolean }
  history: { action: 'send' | 'edit' | 'delete'; text: string | null; created_at: string }[] | null
  reactions: ReactionSummary[]
  request?: {id:string;status:string;role_name:string;reply_text:string|null;can_withdraw:boolean;can_review:boolean}|null
  read_receipt?: string|null
}
export type TimelineItem = Omit<ChatMessage,'source_kind'|'sender_user_id'|'sender_name'|'id'> & {
  id: string
  source: { kind: 'chat'|'private_message'|'role_request'; id:string }
  author: { id:string;name:string;is_host:boolean }
  type: 'message'|'role_request'
}
export type TimelinePage = { items: TimelineItem[]; next_before:string|null; next_changes:string;next_after:string|null }
export type ChangesPage = { changes:{item:TimelineItem;insert:boolean}[]; next_changes:string;has_more:boolean }
export type ChatPolicy = {
  common_send_at_night: boolean
  team_send_in_day: boolean
  dead_common_send: boolean
  dead_team_send: boolean
  slow_mode_seconds: number
}
export type ChannelRef = {
  id: string
  kind: 'common' | 'team' | 'moderator_dm' | 'announcement' | 'vote'
  name: string
  capabilities: { read: boolean; send: boolean; react: boolean; submit_request: boolean }
  readonly_reason: string | null
  team_label: string | null
  participant_user_id: string | null
  departed: boolean
  last_activity_id: string | null
  unread: boolean
}
export type ChannelCatalog = { channels: ChannelRef[] }
export type ChatContext = {
  dm_participants: { user_id: string; display_name: string; departed: boolean }[]
  team_labels: string[]
  writable_team_labels: string[]
  current_team_label: string | null
  policy: ChatPolicy
  next_chat_at: string | null
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
  sequence?: number
  source_event_id: string
  source_sequence?: number
  source_kind: string
  text: string
  created_at: string
  edited_at: string | null
}
export type PublicAnnouncement = {
  id: string
  sequence?: number
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

export class ApiError extends Error {
  constructor(message:string,public status:number,public code?:string){super(message);this.name='ApiError'}
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
    const info=body?.detail
    const detail=typeof info==='string'?info:info&&typeof info==='object'&&'message' in info&&typeof info.message==='string'?info.message:'Yêu cầu không thực hiện được.'
    const code=info&&typeof info==='object'&&'code' in info&&typeof info.code==='string'?info.code:undefined
    throw new ApiError(detail,response.status,code)
  }
  return response.status === 204 ? undefined as T : await response.json() as T
}

export function jsonBody(value: unknown): string {
  return JSON.stringify(value)
}
