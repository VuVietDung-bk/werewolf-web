import { ApiError } from './api'

export type Delivery = { id:string;scopes:string[];channel_ids:string[] }
export type SyncPage = { deliveries:Delivery[];next_cursor:string;has_more:boolean }
export type RoomSignal = { scopes:string[];channelIds:string[];resync:boolean }
export type ConnectionState = 'connecting'|'live'|'fallback'
type Socket = Pick<WebSocket,'onmessage'|'onclose'|'onerror'|'close'>
type Options = {
  fetchPage:(cursor:string|null,signal:AbortSignal)=>Promise<SyncPage>
  connect:(cursor:string|null)=>Socket
  save:(cursor:string|null)=>void
  initialCursor:string|null
  emit:(signal:RoomSignal)=>void
  state:(state:ConnectionState)=>void
  accessLost:(code:number)=>void
}

/** One owner serializes checkpoint updates; stale HTTP and old sockets cannot overwrite them. */
export class RoomRealtimeClient {
  private active=false
  private cursor:string|null
  private socket:Socket|null=null
  private timer:ReturnType<typeof setInterval>|null=null
  private reconnect:ReturnType<typeof setTimeout>|null=null
  private batchTimer:ReturnType<typeof setTimeout>|null=null
  private request:AbortController|null=null
  private generation=0
  private attempts=0
  private live=false
  private seen=new Set<string>()
  private scopes=new Set<string>()
  private channels=new Set<string>()
  private reset=false
  private allChannels=false
  constructor(private options:Options){this.cursor=options.initialCursor}
  start(){
    this.active=true;this.connect()
    this.timer=setInterval(()=>{if(!this.live)void this.poll()},5000)
  }
  stop(){
    this.active=false;this.generation++;this.request?.abort();this.request=null
    this.socket?.close();this.socket=null
    if(this.timer)clearInterval(this.timer)
    if(this.reconnect)clearTimeout(this.reconnect)
    if(this.batchTimer)clearTimeout(this.batchTimer)
    this.options.save(null)
  }
  private save(cursor:string){this.cursor=cursor;this.options.save(cursor)}
  private queue(deliveries:Delivery[],resync=false,all=false){
    this.reset ||= resync
    if(all){
      this.allChannels=true
      for(const scope of ['room','channels','chat','roles','role_requests','vote','announcements','management'])this.scopes.add(scope)
    }
    for(const delivery of deliveries){
      if(this.seen.has(delivery.id))continue
      this.seen.add(delivery.id)
      if(this.seen.size>2048)this.seen.delete(this.seen.values().next().value!)
      for(const scope of delivery.scopes)this.scopes.add(scope)
      for(const channel of delivery.channel_ids)this.channels.add(channel)
    }
    if(!this.scopes.size&&!this.reset)return
    if(this.batchTimer)return
    this.batchTimer=setTimeout(()=>{
      this.batchTimer=null
      if(!this.active)return
      const signal={scopes:[...this.scopes],channelIds:this.allChannels?[]:[...this.channels],resync:this.reset}
      this.scopes.clear();this.channels.clear();this.reset=false;this.allChannels=false
      this.options.emit(signal)
    },40)
  }
  private deny(code:number){
    this.stop();this.options.accessLost(code)
  }
  private resync(){
    this.generation++;this.cursor=null;this.options.save(null);this.seen.clear()
    this.live=false;this.options.state('fallback');this.queue([],true,true)
    const socket=this.socket;this.socket=null;socket?.close()
    this.schedule();void this.poll()
  }
  private schedule(){
    if(!this.active||this.reconnect)return
    this.reconnect=setTimeout(()=>{this.reconnect=null;this.connect()},Math.min(10000,500*2**Math.min(this.attempts++,5)))
  }
  private connect(){
    if(!this.active)return
    this.options.state('connecting')
    let socket:Socket
    try{socket=this.options.connect(this.cursor)}catch{this.options.state('fallback');void this.poll();this.schedule();return}
    this.socket=socket
    socket.onmessage=(event)=>{
      if(!this.active||this.socket!==socket)return
      let frame:{type?:string;cursor?:string;deliveries?:Delivery[]}
      try{frame=JSON.parse(event.data as string)}catch{return}
      if(frame.type==='resync_required'){this.resync();return}
      if(typeof frame.cursor!=='string')return
      if(frame.type==='ready'){
        const fresh=!this.cursor
        this.generation++;this.save(frame.cursor);this.live=true;this.attempts=0
        this.options.state('live');this.queue([],fresh,true)
      }else if(frame.type==='invalidation'&&Array.isArray(frame.deliveries)){
        this.generation++;this.queue(frame.deliveries);this.save(frame.cursor)
      }
    }
    socket.onclose=(event)=>{
      if(!this.active||this.socket!==socket)return
      this.socket=null;this.live=false;this.options.state('fallback')
      if([4401,4403,4404].includes(event.code)){this.deny(event.code);return}
      if(event.code===4409){this.resync();return}
      void this.poll();this.schedule()
    }
    socket.onerror=()=>{if(this.active&&this.socket===socket){this.live=false;this.options.state('fallback');void this.poll()}}
  }
  async poll(){
    if(!this.active||this.request)return
    const request=new AbortController();this.request=request
    const generation=this.generation
    try{
      let more=true
      while(this.active&&generation===this.generation&&more){
        const fresh=!this.cursor
        const page=await this.options.fetchPage(this.cursor,request.signal)
        if(!this.active||generation!==this.generation)return
        this.queue(page.deliveries,fresh,fresh);this.save(page.next_cursor);more=page.has_more
      }
    }catch(cause){
      if(!this.active||generation!==this.generation)return
      if(cause instanceof ApiError){
        if([401,403,404].includes(cause.status)){this.deny(4000+cause.status);return}
        if(cause.code==='chat_resync_required'){
          this.generation++;this.cursor=null;this.options.save(null);this.seen.clear();this.queue([],true,true)
        }
      }
    }finally{if(this.request===request)this.request=null}
  }
}
