import { useState } from 'react'
import { PENDING_CHAT_PREFIX } from './pending'
import { clearPositions } from './positions'

const PREFIX = 'ma-soi-chat-draft:v1:'
export function clearChatSession() {
  clearPositions()
  try {
    for (let i=sessionStorage.length-1;i>=0;i--) {
      const key=sessionStorage.key(i)
      if(key?.startsWith(PREFIX)||key?.startsWith(PENDING_CHAT_PREFIX)||key?.startsWith('ma-soi-chat-read-v2:')
        ||key?.startsWith('ma-soi-room-live-v2:')||key?.startsWith('ma-soi-room-live:')||key?.startsWith('ma-soi-live-cursor:'))sessionStorage.removeItem(key)
    }
  } catch { /* session storage can be disabled */ }
}
function read(key:string):string {
  try { return (sessionStorage.getItem(key)??'').slice(0,2000) } catch { return '' }
}
export function useChatDraft(scope:string):[string,(text:string)=>void,(sent:string)=>void] {
  const [drafts,setDrafts]=useState<Record<string,string>>({})
  const key=PREFIX+scope
  const text=drafts[key]??read(key)
  const update=(value:string)=>{
    setDrafts((current)=>({...current,[key]:value}))
    try { if(value)sessionStorage.setItem(key,value);else sessionStorage.removeItem(key) } catch { /* keep the in-memory draft */ }
  }
  const acknowledge=(sent:string)=>{
    // A delayed ACK must not erase text typed while the previous message was sending.
    setDrafts((current)=> (current[key]??read(key))===sent ? {...current,[key]:''} : current)
    try { if(read(key)===sent)sessionStorage.removeItem(key) } catch { /* optional persistence */ }
  }
  return [text,update,acknowledge]
}
