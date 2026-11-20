import type { ChatMessage, TimelineItem } from '../../api'

export type DisplayMessage = {
  key: string
  kind: 'chat' | 'private' | 'request'
  authorId: string
  authorName: string
  isHost: boolean
  text: string
  createdAt: string
  editedAt: string | null
  deleted: boolean
  phase: string | null
  round: number | null
  original: ChatMessage | null
}

export const messageKey = (message: ChatMessage) => `${message.source_kind ?? 'chat'}:${message.id}`

export function chatDisplay(message: ChatMessage, hostId: string): DisplayMessage {
  return { key: messageKey(message), kind: message.source_kind === 'role_request'?'request':message.source_kind === 'private_message' ? 'private' : 'chat', authorId: message.sender_user_id,
    authorName: message.sender_user_id === hostId ? 'Quản trò' : message.sender_name,
    isHost: message.sender_user_id === hostId, text: message.text, createdAt: message.created_at,
    editedAt: message.edited_at, deleted: message.deleted, phase: message.phase_type ?? null,
    round: message.round_number ?? null, original: message }
}
const dateFormatter=new Intl.DateTimeFormat('vi-VN')
export function dayKey(value: string): string { return dateFormatter.format(new Date(value)) }
export function canGroup(previous: DisplayMessage | undefined, next: DisplayMessage): boolean {
  if (!previous || previous.deleted || next.deleted) return false
  if(previous.kind==='request'||next.kind==='request')return false
  const gap = Date.parse(next.createdAt) - Date.parse(previous.createdAt)
  return previous.authorId === next.authorId && previous.kind === next.kind &&
    previous.phase === next.phase && previous.round === next.round &&
    dayKey(previous.createdAt) === dayKey(next.createdAt) && gap >= 0 && gap <= 5 * 60 * 1000
}
export function phaseText(item: DisplayMessage): string | null {
  return item.phase && item.round !== null ? `${item.phase === 'night' ? 'Đêm' : 'Ngày'} ${item.round}` : null
}

export function timelineMessage(item:TimelineItem):ChatMessage {
  return {source_kind:item.source.kind,id:item.source.id,sender_user_id:item.author.id,sender_name:item.author.name,
    text:item.text,created_at:item.created_at,edited_at:item.edited_at,deleted:item.deleted,
    phase_type:item.phase_type,round_number:item.round_number,actions:item.actions,history:item.history,reactions:item.reactions??[],request:item.request,read_receipt:item.read_receipt}
}
