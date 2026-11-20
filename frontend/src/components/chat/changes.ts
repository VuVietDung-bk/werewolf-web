import type { ChatMessage, ChangesPage, TimelineItem } from '../../api'
import { messageKey, timelineMessage } from './presentation'

/** Server controls insert order; edits replace in place and never append unloaded old history. */
export function applyChanges(current:ChatMessage[],changes:ChangesPage['changes']):ChatMessage[] {
  const items=new Map(current.map(item=>[messageKey(item),item]))
  for(const change of changes){
    const item=timelineMessage(change.item),key=messageKey(item)
    if(item.deleted&&!item.history){items.delete(key);continue}
    if(change.insert||items.has(key))items.set(key,item)
  }
  return [...items.values()]
}
export function prependHistory(current:ChatMessage[],older:TimelineItem[],revisions:ReadonlyMap<string,TimelineItem>=new Map()):ChatMessage[] {
  const known=new Set(current.map(messageKey))
  return [...older.map(item=>revisions.get(item.id)??item).map(timelineMessage)
    .filter(item=>(!item.deleted||item.history)&&!known.has(messageKey(item))),...current]
}
