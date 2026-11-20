import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'

class ApiError extends Error {constructor(status,code){super('API failed');this.status=status;this.code=code}}
const source=readFileSync(new URL('../src/roomRealtime.ts',import.meta.url),'utf8')
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve()}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
function fixture(initialCursor=null){
 const timers=new Map();let id=0,time=0
 const clock={
  set:(callback,delay,interval=false)=>{const key=++id;timers.set(key,{callback,at:time+delay,delay,interval});return key},
  clear:key=>timers.delete(key),
  advance:async(delta)=>{
   const target=time+delta
   while(true){
    const next=[...timers].sort((a,b)=>a[1].at-b[1].at).find(([,item])=>item.at<=target)
    if(!next)break
    const [key,item]=next;time=item.at
    if(item.interval)item.at+=item.delay;else timers.delete(key)
    item.callback();await flush()
   }
   time=target;await flush()
  },
 }
 const sandbox={exports:{},require:()=>({ApiError}),AbortController,
  setTimeout:(fn,delay)=>clock.set(fn,delay),clearTimeout:clock.clear,
  setInterval:(fn,delay)=>clock.set(fn,delay,true),clearInterval:clock.clear}
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,sandbox)
 const sockets=[],signals=[],states=[],saved=[],denied=[],requests=[]
 let fetchPage=async cursor=>({deliveries:[],next_cursor:cursor??'bootstrap',has_more:false})
 const client=new sandbox.exports.RoomRealtimeClient({initialCursor,
  fetchPage:(cursor,signal)=>{requests.push({cursor,signal});return fetchPage(cursor,signal)},
  connect:cursor=>{const socket={cursor,close(){this.closed=true;this.onclose?.({code:1000})},
   frame(frame){this.onmessage?.({data:JSON.stringify(frame)})},disconnect(code=1006){this.onclose?.({code})}}
   sockets.push(socket);return socket},
  save:cursor=>saved.push(cursor),emit:signal=>signals.push(JSON.parse(JSON.stringify(signal))),
  state:state=>states.push(state),accessLost:code=>denied.push(code)})
 return {client,sockets,signals,states,saved,denied,requests,clock,setFetch:fn=>{fetchPage=fn}}
}
const delivery=(id,scope='chat',channel='own')=>({id,scopes:[scope],channel_ids:[channel]})

test('one connection, batched invalidations, duplicate IDs and cleanup',async()=>{
 const f=fixture();f.client.start();assert.equal(f.sockets.length,1)
 const socket=f.sockets[0];socket.frame({type:'ready',cursor:'initial'})
 await f.clock.advance(40);assert.equal(f.signals[0].resync,true)
 socket.frame({type:'invalidation',cursor:'next-a',deliveries:[delivery('a')]})
 socket.frame({type:'invalidation',cursor:'next-b',deliveries:[delivery('a'),delivery('b','roles')]})
 await f.clock.advance(40)
 assert.deepEqual(f.signals[1],{scopes:['chat','roles'],channelIds:['own'],resync:false})
 assert.equal(f.saved.at(-1),'next-b')
 f.client.stop();socket.frame({type:'invalidation',cursor:'must-ignore',deliveries:[delivery('late')]})
 await f.clock.advance(30000)
 assert.equal(f.sockets.length,1);assert.equal(f.saved.at(-1),null)
})

test('late HTTP response cannot overwrite a newer WebSocket checkpoint or scopes',async()=>{
 const f=fixture('old');f.client.start();const socket=f.sockets[0]
 socket.frame({type:'ready',cursor:'old'});await f.clock.advance(40)
 const request=deferred();f.setFetch(()=>request.promise)
 const polling=f.client.poll()
 socket.frame({type:'invalidation',cursor:'newer-ws',deliveries:[delivery('new')]})
 request.resolve({deliveries:[delivery('stale','chat','wrong-channel')],next_cursor:'older-http',has_more:false})
 await polling;await f.clock.advance(40)
 assert.equal(f.saved.at(-1),'newer-ws')
 assert.deepEqual(f.signals.at(-1).channelIds,['own'])
 f.client.stop()
})

test('fallback drains pages and reconnect resumes from latest opaque token; old socket is inert',async()=>{
 const f=fixture('before');f.client.start();const first=f.sockets[0]
 first.frame({type:'ready',cursor:'before'});await f.clock.advance(40)
 let page=0
 f.setFetch(async()=>++page===1?{deliveries:[delivery('a'),delivery('b')],next_cursor:'middle',has_more:true}
  :{deliveries:[delivery('c')],next_cursor:'after',has_more:false})
 first.disconnect();await flush();await f.clock.advance(40)
 assert.deepEqual(f.requests.map(item=>item.cursor),['before','middle'])
 assert.equal(f.saved.at(-1),'after')
 await f.clock.advance(500)
 assert.equal(f.sockets.length,2);assert.equal(f.sockets[1].cursor,'after')
 first.frame({type:'invalidation',cursor:'stale-socket',deliveries:[]})
 assert.equal(f.saved.at(-1),'after')
 f.client.stop()
})

test('resync clears stale checkpoint and caches, bootstraps HTTP and reconnects',async()=>{
 const f=fixture('bad');f.client.start();f.sockets[0].frame({type:'resync_required'})
 await flush();await f.clock.advance(40)
 assert.equal(f.requests[0].cursor,null)
 assert.equal(f.signals[0].resync,true)
 assert.equal(f.saved.at(-1),'bootstrap')
 await f.clock.advance(500);assert.equal(f.sockets[1].cursor,'bootstrap')
 f.client.stop()
})

test('stop aborts pending HTTP and prevents late data from a previous identity',async()=>{
 const f=fixture();f.client.start();const held=deferred();f.setFetch(()=>held.promise)
 const polling=f.client.poll();f.client.stop()
 assert.equal(f.requests[0].signal.aborted,true)
 held.resolve({deliveries:[delivery('old-user')],next_cursor:'old-user-token',has_more:false})
 await polling;await f.clock.advance(6000)
 assert.equal(f.signals.length,0);assert.equal(f.saved.at(-1),null)
})

test('HTTP and WS revocation stop reconnect, clear token and notify owner once',async()=>{
 for(const via of ['http','ws']){
  const f=fixture('private');f.client.start()
  if(via==='http'){f.setFetch(async()=>{throw new ApiError(403)});await f.client.poll()}
  else f.sockets[0].disconnect(4403)
  await f.clock.advance(30000)
  assert.deepEqual(f.denied,[4403]);assert.equal(f.saved.at(-1),null);assert.equal(f.sockets.length,1)
 }
})

test('HTTP resync rejection clears old token and the next fallback bootstraps a new boundary',async()=>{
 const f=fixture('expired');f.client.start();let attempts=0
 f.setFetch(async()=>{if(++attempts===1)throw new ApiError(409,'chat_resync_required')
  return {deliveries:[],next_cursor:'fresh-boundary',has_more:false}})
 await f.client.poll();await f.clock.advance(40)
 assert.equal(f.saved.at(-1),null);assert.equal(f.signals.at(-1).resync,true)
 await f.clock.advance(5000)
 assert.equal(f.requests.at(-1).cursor,null);assert.equal(f.saved.at(-1),'fresh-boundary')
 f.client.stop()
})

test('ready wildcard is preserved when coalesced with a different channel invalidation',async()=>{
 const f=fixture('cached');f.client.start();const socket=f.sockets[0]
 socket.frame({type:'ready',cursor:'cached'})
 socket.frame({type:'invalidation',cursor:'next',deliveries:[delivery('other','chat','another-dm')]})
 await f.clock.advance(40)
 assert.deepEqual(f.signals[0].channelIds,[])
 assert.equal(f.signals[0].resync,false)
 assert.ok(f.signals[0].scopes.includes('chat'))
 f.client.stop()
})
