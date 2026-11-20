type Entry={item:string;acknowledged:string|null;timer:ReturnType<typeof setTimeout>|null;running:boolean;request:AbortController|null}
type Options={send:(channel:string,item:string,signal:AbortSignal)=>Promise<void>;changed:()=>void;visible:()=>boolean}

/** Coalesce visible checkpoints; the server orders them, never the client UUIDs. */
export class ReadQueue {
  private active=true
  private entries=new Map<string,Entry>()
  constructor(private options:Options){}
  push(channel:string,item:string){
    if(!this.active)return
    let entry=this.entries.get(channel)
    if(!entry){entry={item,acknowledged:null,timer:null,running:false,request:null};this.entries.set(channel,entry)}
    entry.item=item
    if(entry.acknowledged!==item&&!entry.timer&&!entry.running)this.schedule(channel,entry,250)
  }
  private schedule(channel:string,entry:Entry,delay:number){
    entry.timer=setTimeout(()=>{entry.timer=null;void this.flush(channel,entry)},delay)
  }
  private async flush(channel:string,entry:Entry){
    if(!this.active||!this.options.visible())return
    const item=entry.item,request=new AbortController()
    entry.running=true;entry.request=request
    let failed=false
    try{
      await this.options.send(channel,item,request.signal)
      if(!this.active)return
      entry.acknowledged=item;this.options.changed()
    }catch{failed=true}finally{
      entry.running=false;entry.request=null
      if(this.active&&entry.acknowledged!==entry.item)this.schedule(channel,entry,failed?3000:250)
    }
  }
  stop(){
    this.active=false
    for(const entry of this.entries.values()){if(entry.timer)clearTimeout(entry.timer);entry.request?.abort()}
    this.entries.clear()
  }
}
