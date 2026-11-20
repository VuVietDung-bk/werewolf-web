import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'
function moduleAt(name,requires={}){
 const source=readFileSync(new URL(`../src/components/chat/${name}.ts`,import.meta.url),'utf8')
 const sandbox={exports:{},require:name=>requires[name]}
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,sandbox)
 return sandbox.exports
}
const presentation=moduleAt('presentation')
const {applyChanges,prependHistory}=moduleAt('changes',{'./presentation':presentation})
const item=(id,text=id,kind='chat')=>({id:`${kind}:${id}`,source:{kind,id},author:{id:'a',name:'A',is_host:false},text,
 created_at:'2026-11-19T10:00:00Z',edited_at:null,deleted:false,history:null,actions:{edit:false,delete:false,react:false}})
const row=id=>presentation.timelineMessage(item(id))
test('insert order is server order; revision updates remain in place and unloaded old sources stay unloaded',()=>{
 const next=applyChanges([row('a'),row('b')],[{item:item('old-unloaded'),insert:false},
 {item:item('c'),insert:true},{item:item('a','Edited A'),insert:false},{item:item('d'),insert:true}])
 assert.equal(JSON.stringify(next.map(x=>x.id)),JSON.stringify(['a','b','c','d']))
 assert.equal(next[0].text,'Edited A')
 const again=applyChanges(next,[{item:item('c'),insert:true}])
 assert.equal(again.length,4)
})
test('source kind prevents UUID collisions; deletes remove only the selected source',()=>{
 const next=applyChanges([row('same')],[{item:item('same','PM','private_message'),insert:true}])
 assert.equal(next.length,2)
 const deleted=applyChanges(next,[{item:{...item('same'),deleted:true,text:''},insert:false}])
 assert.equal(deleted.length,1)
 assert.equal(deleted[0].source_kind,'private_message')
 const afterGame=applyChanges(deleted,[{item:{...item('same'),deleted:true,text:'',history:[{action:'send',text:'Original'}]},insert:true}])
 assert.equal(afterGame.length,2)
})
test('loading history prepends without overwriting a newer in-flight revision',()=>{
 const current=applyChanges([row('a')],[{item:item('a','Latest edit'),insert:false}])
 const next=prependHistory(current,[item('old'),item('a','Stale page')])
 assert.equal(JSON.stringify(next.map(x=>x.id)),JSON.stringify(['old','a']))
 assert.equal(next[1].text,'Latest edit')
})
test('an unloaded source changed during an old-page request is reconciled before insertion',()=>{
 const fresh=item('old','Edited while history was in flight')
 const buffer=new Map([[fresh.id,fresh],['chat:deleted',{...item('deleted'),deleted:true,text:''}]])
 const next=prependHistory([row('current')],[item('old','Stale page value'),item('deleted')],buffer)
 assert.equal(JSON.stringify(next.map(x=>x.id)),JSON.stringify(['old','current']))
 assert.equal(next[0].text,'Edited while history was in flight')
})
test('reaction updates replace counts in place and an empty aggregate clears removed reactions',()=>{
 const changed={...item('a'),reactions:[{emoji:'👨‍👩‍👧‍👦',count:2,mine:true}]}
 const next=applyChanges([row('a'),row('b')],[{item:changed,insert:false}])
 assert.equal(next[0].reactions[0].count,2)
 assert.equal(next[0].reactions[0].mine,true)
 assert.equal(JSON.stringify(next.map(x=>x.id)),JSON.stringify(['a','b']))
 const removed=applyChanges(next,[{item:{...changed,reactions:[]},insert:false}])
 assert.equal(removed[0].reactions.length,0)
})

test('role request revisions preserve one source, its slot, reply and actor-scoped read receipt',()=>{
 const pending={...item('request','Xin soi','role_request'),type:'role_request',read_receipt:'receipt-one',
  request:{id:'request',status:'pending',reply_text:null,can_review:false,can_withdraw:true}}
 const current=[row('chat-before'),presentation.timelineMessage(pending),row('chat-after')]
 const accepted={...pending,read_receipt:'receipt-two',request:{...pending.request,status:'accepted',reply_text:'Đã xác nhận',can_withdraw:false}}
 const changed=applyChanges(current,[{item:accepted,insert:false}])
 assert.equal(changed.length,3)
 assert.equal(changed[1].request.status,'accepted')
 assert.equal(changed[1].request.reply_text,'Đã xác nhận')
 assert.equal(changed[1].read_receipt,'receipt-two')
 assert.equal(changed[0].id,'chat-before');assert.equal(changed[2].id,'chat-after')
 assert.equal(presentation.chatDisplay(changed[1],'host').kind,'request')
 assert.equal(presentation.canGroup(presentation.chatDisplay(changed[1],'host'),presentation.chatDisplay(changed[1],'host')),false)
})
