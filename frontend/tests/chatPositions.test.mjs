import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'
const source=ts.transpileModule(readFileSync(new URL('../src/components/chat/positions.ts',import.meta.url),'utf8'),{
 compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
function load(storage){
 const sandbox={exports:{},sessionStorage:storage,setTimeout:()=>1,clearTimeout:()=>{}}
 vm.runInNewContext(source,sandbox);return sandbox.exports
}
const key='chat:11111111-1111-4111-8111-111111111111'
const anchor={key,offset:-17,top:5000,atEnd:false}
function storage(){const map=new Map();return {map,getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,value),removeItem:key=>map.delete(key)}}
test('reload restores only source ID/geometry, scopes users separately and bounds metadata to 128',()=>{
 const data=storage(),first=load(data)
 first.rememberPosition('user/room/channel',anchor);first.flushPositions()
 assert.equal(JSON.stringify(load(data).positionFor('user/room/channel')),JSON.stringify(anchor))
 assert.equal(load(data).positionFor('other/room/channel'),undefined)
 for(let i=0;i<200;i++)first.rememberPosition('scope-'+i,anchor)
 first.flushPositions();const entries=JSON.parse(data.getItem(first.POSITION_KEY))
 assert.equal(entries.length,128)
 assert.equal(first.positionFor('scope-0'),undefined)
 assert.equal(JSON.stringify(entries).includes('message text'),false)
 const restored=load(data);restored.clearPositions()
 assert.equal(restored.positionFor('scope-199'),undefined)
 assert.equal(data.getItem(first.POSITION_KEY),null)
})
test('invalid or disabled storage does not affect chat; untrusted IDs and nonfinite geometry are ignored',()=>{
 const data=storage(),first=load(data)
 data.setItem(first.POSITION_KEY,JSON.stringify([['bad',{...anchor,key:'<script>'}],['nan',{...anchor,top:null}],['good',anchor]]))
 const next=load(data);assert.equal(next.positionFor('bad'),undefined);assert.equal(next.positionFor('nan'),undefined)
 assert.equal(next.positionFor('good').key,key)
 next.rememberPosition('infinite',{...anchor,offset:Infinity});assert.equal(next.positionFor('infinite'),undefined)
 const broken=load({getItem:()=>{throw Error()},setItem:()=>{throw Error()},removeItem:()=>{throw Error()}})
 broken.rememberPosition('local',anchor);broken.flushPositions();assert.equal(broken.positionFor('local').key,key)
 broken.forgetPosition('local');assert.equal(broken.positionFor('local'),undefined)
})
test('a revoked session cannot restore old positions through a late ACK or unmount cleanup',()=>{
 const data=storage(),positions=load(data),epoch=positions.positionGeneration()
 positions.rememberPosition('old-user',anchor,epoch);positions.flushPositions()
 positions.clearPositions()
 positions.rememberPosition('old-user',anchor,epoch);positions.flushPositions()
 assert.equal(positions.positionFor('old-user'),undefined)
 assert.equal(data.getItem(positions.POSITION_KEY),null)
 positions.rememberPosition('new-user',anchor,positions.positionGeneration());positions.flushPositions()
 assert.equal(positions.positionFor('new-user').key,key)
})
