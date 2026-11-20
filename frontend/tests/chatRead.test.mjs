import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'

const flush=async()=>{for(let i=0;i<10;i++)await Promise.resolve()}
function load(name,globals={}){
 const sandbox={exports:{},...globals}
 vm.runInNewContext(ts.transpileModule(readFileSync(new URL(`../src/components/chat/${name}.ts`,import.meta.url),'utf8'),{
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,sandbox)
 return sandbox.exports
}
const {readableRow}=load('visibleRead')
test('viewport reads require half of a short row or the end of a tall row',()=>{
 assert.equal(readableRow(80,120,100,500),true)
 assert.equal(readableRow(70,120,100,500),false)
 assert.equal(readableRow(500,550,100,500),false)
 assert.equal(readableRow(0,90,100,500),false)
 assert.equal(readableRow(100,900,100,500),false)
 assert.equal(readableRow(-400,400,100,500),true)
 assert.equal(readableRow(100,100,100,500),false)
})
test('hidden documents and an open modal cannot acknowledge background chat',()=>{
 const document={visibilityState:'visible',querySelector:()=>null}
 const {chatIsVisible}=load('visibleRead',{document})
 assert.equal(chatIsVisible(),true)
 document.visibilityState='hidden';assert.equal(chatIsVisible(),false)
 document.visibilityState='visible';document.querySelector=()=>({open:true});assert.equal(chatIsVisible(),false)
})

function fixture(){
 let now=0,id=0,visible=true;const timers=new Map(),sent=[],changed=[]
 const clock={advance:async delta=>{
  const target=now+delta
  while(true){
   const next=[...timers].sort((a,b)=>a[1].time-b[1].time).find(([,task])=>task.time<=target)
   if(!next)break
   const [key,task]=next;now=task.time;timers.delete(key);task.run();await flush()
  }
  now=target;await flush()
 }}
 let sender=async()=>{}
 const {ReadQueue}=load('readQueue',{AbortController,
  setTimeout:(run,delay)=>{const key=++id;timers.set(key,{time:now+delay,run});return key},clearTimeout:key=>timers.delete(key)})
 const queue=new ReadQueue({send:(channel,item,signal)=>{sent.push({channel,item,signal});return sender(channel,item,signal)},
  changed:()=>changed.push(true),visible:()=>visible})
 return {queue,clock,sent,changed,setSender:next=>{sender=next},setVisible:next=>{visible=next}}
}
test('visible positions coalesce without comparing UUIDs and duplicate success does not send again',async()=>{
 const f=fixture();f.queue.push('dm','z');f.queue.push('dm','a');await f.clock.advance(250)
 assert.deepEqual(f.sent.map(item=>item.item),['a'])
 f.queue.push('dm','a');await f.clock.advance(5000);assert.equal(f.sent.length,1)
 f.setVisible(false);f.queue.push('dm','new');await f.clock.advance(250);assert.equal(f.sent.length,1)
 f.setVisible(true);f.queue.push('dm','new');await f.clock.advance(250);assert.equal(f.sent.length,2)
 f.queue.stop()
})
test('new visible item while an ACK is pending waits for that request before sending',async()=>{
 const f=fixture();let release
 f.setSender(()=>new Promise(resolve=>{release=resolve}))
 f.queue.push('general','older');await f.clock.advance(250)
 f.queue.push('general','newer');await f.clock.advance(250);assert.equal(f.sent.length,1)
 f.setSender(async()=>{});release();await flush();await f.clock.advance(250)
 assert.deepEqual(f.sent.map(item=>item.item),['older','newer']);f.queue.stop()
})
test('transient failure retries and dispose aborts old-user work without a late refresh',async()=>{
 const f=fixture();let attempts=0
 f.setSender(async()=>{if(++attempts===1)throw new Error('offline')})
 f.queue.push('team','one');await f.clock.advance(250);assert.equal(f.changed.length,0)
 await f.clock.advance(3000);assert.equal(f.changed.length,1)
 let release;f.setSender(()=>new Promise(resolve=>{release=resolve}))
 f.queue.push('team','two');await f.clock.advance(250);f.queue.stop()
 assert.equal(f.sent.at(-1).signal.aborted,true)
 release();await flush();await f.clock.advance(10000);assert.equal(f.changed.length,1)
})
