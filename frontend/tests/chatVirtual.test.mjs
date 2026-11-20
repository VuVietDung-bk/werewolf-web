import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'
function load(name,requires={}){
 const sandbox={exports:{},require:key=>requires[key]}
 vm.runInNewContext(ts.transpileModule(readFileSync(new URL(`../src/components/chat/${name}.ts`,import.meta.url),'utf8'),{
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,sandbox)
 return sandbox.exports
}
const presentation=load('presentation')
const {timelineEntries,offsetsFor,rowAt,windowIndices}=load('virtualLayout',{'./presentation':presentation})
const item=(i,text='Tin 🐺')=>({key:`chat:${i}`,kind:'chat',authorId:'a',createdAt:'2026-11-20T10:00:00Z',
 text,deleted:false,phase:'day',round:0,original:{reactions:[]}})
test('10,000 rows keep a finite window at start, middle and end; pinned editor stays mounted',()=>{
 const entries=timelineEntries(Array.from({length:10000},(_,i)=>item(i)))
 const offsets=offsetsFor(entries,new Map(),800)
 assert.equal(entries.length,10001)
 for(const top of [0,offsets[5000],offsets.at(-1)-800]){
  const rendered=windowIndices(offsets,top,800,[5])
  assert.ok(rendered.length<100)
  assert.ok(rendered.includes(5))
  assert.ok(rendered.includes(rowAt(offsets,top)))
 }
})
test('measured height changes move following coordinates; boundaries and giant rows remain reachable',()=>{
 const entries=timelineEntries([item(0),item(1,'very long\n'.repeat(1000)),item(2)])
 const offsets=offsetsFor(entries,new Map([['chat:0',150],['chat:1',22000]]),320)
 const long=entries.findIndex(row=>row.key==='chat:1')
 assert.equal(rowAt(offsets,offsets[long]),long)
 assert.equal(rowAt(offsets,offsets[long]+21999),long)
 assert.equal(rowAt(offsets,offsets[long+1]),long+1)
 assert.equal(rowAt(offsets,1e9),entries.length-1)
 assert.equal(offsetsFor([],new Map(),320).length,1)
 assert.equal(windowIndices([0],0,800).length,0)
})
test('group and marker context comes from full history even when the previous row is unmounted',()=>{
 const rows=Array.from({length:200},(_,i)=>item(i))
 rows[100]={...rows[100],phase:'night',round:1}
 const entries=timelineEntries(rows)
 assert.equal(entries.find(row=>row.key==='chat:99').grouped,true)
 assert.equal(entries.find(row=>row.key==='chat:100').grouped,false)
 assert.equal(entries.find(row=>row.key==='marker:chat:100').marker,'Đêm 1')
 assert.equal(entries.find(row=>row.key==='chat:101').grouped,false)
})
