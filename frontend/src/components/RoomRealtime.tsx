import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { RoomRealtimeClient, type ConnectionState, type RoomSignal, type SyncPage } from '../roomRealtime'

type Realtime = {state:ConnectionState;subscribe:(callback:(signal:RoomSignal)=>void)=>()=>void}
export const RoomRealtimeContext=createContext<Realtime|null>(null)

export function useRoomRealtime(roomId:string,userId:string,onRefresh:()=>void,onAccessLost:(code:number)=>void):Realtime {
  const [state,setState]=useState<ConnectionState>('connecting')
  const listeners=useRef(new Set<(signal:RoomSignal)=>void>())
  const callbacks=useRef({onRefresh,onAccessLost});callbacks.current={onRefresh,onAccessLost}
  const subscribe=useCallback((callback:(signal:RoomSignal)=>void)=>{
    listeners.current.add(callback);return()=>{listeners.current.delete(callback)}
  },[])
  useEffect(()=>{
    const key=`ma-soi-room-live-v2:${roomId}:${userId}`
    let initialCursor:string|null=null
    try{initialCursor=sessionStorage.getItem(key)}catch{/* optional persistence */}
    const client=new RoomRealtimeClient({initialCursor,
      fetchPage:(cursor,signal)=>api<SyncPage>(`/api/v2/rooms/${roomId}/sync${cursor?`?cursor=${encodeURIComponent(cursor)}`:''}`,{signal}),
      connect:(cursor)=>new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/v2/rooms/${roomId}/live${cursor?`?cursor=${encodeURIComponent(cursor)}`:''}`),
      save:(cursor)=>{try{if(cursor)sessionStorage.setItem(key,cursor);else sessionStorage.removeItem(key)}catch{/* optional persistence */}},
      state:setState,accessLost:(code)=>callbacks.current.onAccessLost(code),
      emit:(signal)=>{
        if(signal.resync||signal.scopes.some(scope=>scope==='room'||scope==='management'))callbacks.current.onRefresh()
        for(const callback of listeners.current)callback(signal)
      },
    })
    client.start()
    const visible=()=>{if(!document.hidden)void client.poll()}
    window.addEventListener('focus',visible);document.addEventListener('visibilitychange',visible)
    return()=>{client.stop();window.removeEventListener('focus',visible);document.removeEventListener('visibilitychange',visible)}
  },[roomId,userId])
  return {state,subscribe}
}

/** Components reload through their existing guarded effects, including private updates. */
export function useRoomSignal(scopes:string[]):number {
  const realtime=useContext(RoomRealtimeContext)
  const [version,setVersion]=useState(0)
  const key=scopes.join(',')
  useEffect(()=>realtime?.subscribe(signal=>{
    if(signal.resync||signal.scopes.some(scope=>key.split(',').includes(scope)))setVersion(value=>value+1)
  }),[realtime?.subscribe,key])
  return version
}
