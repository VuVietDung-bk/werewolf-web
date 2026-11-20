import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, jsonBody, type ChannelCatalog, type ChatContext, type Room } from '../../api'
import type { ConnectionState, RoomSignal } from '../../roomRealtime'
import { ReadQueue } from './readQueue'
import { chatIsVisible } from './visibleRead'

type Live = {state:ConnectionState;subscribe:(callback:(signal:RoomSignal)=>void)=>()=>void}
type SharedChannels = Live & { markRead:(channel:string,item:string,receipt?:string|null)=>void;catalog: ChannelCatalog | null; context: ChatContext | null; error: string; refresh: () => Promise<void> }
const ChannelContext = createContext<SharedChannels | null>(null)

export function useChannelCatalog(room: Room, userId: string, realtime:Live): SharedChannels {
  const [state, setState] = useState<{catalog:ChannelCatalog|null;context:ChatContext|null;error:string}>({catalog:null,context:null,error:''})
  const refreshRef = useRef<() => Promise<void>>(async () => {})
  const queue=useRef<ReadQueue|null>(null)
  useEffect(()=>{
    const next=new ReadQueue({
      send:async(channel,item,signal)=>{
        const [id,receipt]=item.split('@')
        try{await api<void>(`/api/v2/rooms/${room.id}/channels/${channel}/read`,{
          method:'POST',body:jsonBody({visible_item_id:id,...(receipt?{read_receipt:receipt}:{})}),signal,
        })}catch(cause){if(!(cause instanceof ApiError&&[401,403,404,409].includes(cause.status)))throw cause}
      },
      changed:()=>{void refreshRef.current()},visible:chatIsVisible,
    })
    queue.current=next
    return()=>{next.stop();if(queue.current===next)queue.current=null}
  },[room.id,userId])
  const markRead=useCallback((channel:string,item:string,receipt?:string|null)=>{queue.current?.push(channel,receipt?`${item}@${receipt}`:item)},[])
  useEffect(() => {
    let active = true, fetching = false, again = false
    setState({catalog:null,context:null,error:''})
    async function refresh() {
      if (fetching) { again = true; return }
      fetching = true
      try {
        const [catalog, context] = await Promise.all([
          api<ChannelCatalog>(`/api/v2/rooms/${room.id}/channels`),
          api<ChatContext>(`/api/rooms/${room.id}/chat/context`),
        ])
        if (active) setState({catalog,context,error:''})
      } catch (cause) {
        // Fail closed: do not keep send controls from a previous successful permission snapshot.
        if (active) setState({catalog:null,context:null,error:cause instanceof Error ? cause.message : 'Không tải được quyền kênh.'})
      } finally {
        fetching = false
        if (active && again) { again = false; await refresh() }
      }
    }
    refreshRef.current = refresh
    void refresh()
    const timer = window.setInterval(() => void refresh(),6000)
    return () => { active=false;window.clearInterval(timer);refreshRef.current=async()=>{} }
  },[room.id,userId])
  const previousVersion = useRef(room.version)
  useEffect(() => {
    if(previousVersion.current!==room.version){previousVersion.current=room.version;void refreshRef.current()}
  },[room.version])
  const refresh=useCallback(()=>refreshRef.current(),[])
  useEffect(()=>realtime.subscribe(signal=>{
    if(signal.resync)setState({catalog:null,context:null,error:''})
    if(signal.resync||signal.scopes.includes('channels'))void refreshRef.current()
  }),[realtime.subscribe])
  return {...state,...realtime,refresh,markRead}
}

export function ChannelProvider({value,children}:{value:SharedChannels;children:ReactNode}) {
  return <ChannelContext.Provider value={value}>{children}</ChannelContext.Provider>
}
export function useRoomChannels(): SharedChannels {
  const value=useContext(ChannelContext)
  if(!value)throw new Error('Chat requires a room channel provider')
  return value
}
