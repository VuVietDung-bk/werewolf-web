import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'

function moduleAt(name, extras={}) {
  const source=readFileSync(new URL(`../src/components/chat/${name}.ts`,import.meta.url),'utf8')
  const sandbox={exports:{},...extras}
  vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,sandbox)
  return sandbox.exports
}
const {canGroup,phaseText,chatDisplay}=moduleAt('presentation')
const message={key:'1',kind:'chat',authorId:'a',phase:'day',round:0,createdAt:'2026-11-19T10:00:00Z',deleted:false}

test('group authors in the same day, phase and source within five minutes',()=>{
  assert.ok(canGroup(message,{...message,key:'2',createdAt:'2026-11-19T10:05:00Z'}))
  for(const patch of [{authorId:'b'},{kind:'private'},{phase:'night'},{round:1},{deleted:true},
    {createdAt:'2026-11-19T10:05:01Z'},{createdAt:'2026-11-19T09:59:59Z'},{createdAt:'2026-11-20T10:00:00Z'}]) {
    assert.equal(canGroup(message,{...message,...patch}),false,JSON.stringify(patch))
  }
})
test('phase markers omit unavailable metadata',()=>{
  assert.equal(phaseText(message),'Ngày 0')
  assert.equal(phaseText({...message,phase:'night',round:1}),'Đêm 1')
  assert.equal(phaseText({...message,phase:null}),null)
})
test('different sources have distinct keys and preserve literal content',()=>{
  const text='<img src=x onerror=alert(1)> 👨‍👩‍👧‍👦'
  const chat=chatDisplay({id:'same',sender_user_id:'h',text,phase_type:'day',round_number:0},'h')
  const pm=chatDisplay({id:'same',sender_user_id:'h',text,source_kind:'private_message'},'h')
  assert.notEqual(chat.key,pm.key)
  assert.equal(chat.text,text)
  assert.equal(chat.isHost,true)
  assert.equal(pm.phase,null)
})
const data=new Map()
const localStorage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value)}
const {findEmoji,expandEmojiAliases,rememberEmoji,recentEmoji,RECENT_EMOJI_KEY}=moduleAt('emoji',{localStorage})
test('search handles Vietnamese accents, uppercase Đ and aliases',()=>{
  assert.ok(findEmoji('SÓI').some(e=>e.value==='🐺'))
  assert.ok(findEmoji('ĐỒNG Ý').some(e=>e.value==='👍'))
  assert.equal(findEmoji(':family:')[0].value,'👨‍👩‍👧‍👦')
  assert.equal(findEmoji('does-not-exist').length,0)
  assert.equal(expandEmojiAliases('a :WOLF: :family: :unknown: <script>'),'a 🐺 👨‍👩‍👧‍👦 :unknown: <script>')
})
test('recent emoji tolerate invalid storage and deduplicate choices',()=>{
  data.set(RECENT_EMOJI_KEY,'broken')
  assert.equal(recentEmoji().length,0)
  data.set(RECENT_EMOJI_KEY,'["<script>","🐺",null]')
  assert.equal(recentEmoji().length,1)
  rememberEmoji('👨‍👩‍👧‍👦');rememberEmoji('🐺');rememberEmoji('🐺')
  assert.equal(JSON.stringify(recentEmoji().map(e=>e.value)),JSON.stringify(['🐺','👨‍👩‍👧‍👦']))
})
const sessionData=new Map()
const sessionStorage={getItem:key=>sessionData.get(key)??null,setItem:(key,value)=>sessionData.set(key,value),removeItem:key=>sessionData.delete(key)}
const {pendingChatRequest}=moduleAt('pending',{sessionStorage})
test('pending send IDs survive a module reload and stay scoped to user/room/channel',()=>{
  const first='11111111-1111-4111-8111-111111111111'
  const second='22222222-2222-4222-8222-222222222222'
  const request=pendingChatRequest('host/room/general',{text:'secret'},first)
  const reloaded=moduleAt('pending',{sessionStorage})
  assert.equal(reloaded.pendingChatRequest('host/room/general',{text:'secret'},second).id,first)
  assert.equal(reloaded.pendingChatRequest('other/room/general',{text:'secret'},second).id,second)
  const next=pendingChatRequest('host/room/general',{text:'new draft'},second)
  request.acknowledge()
  assert.equal(pendingChatRequest('host/room/general',{text:'new draft'},first).id,second)
  next.acknowledge()
  assert.equal(pendingChatRequest('host/room/general',{text:'new draft'},first).id,first)
})
test('addressed sends retain a matching legacy pending ID during the protocol upgrade',()=>{
  const first='33333333-3333-4333-8333-333333333333'
  const second='44444444-4444-4444-8444-444444444444'
  const legacy={channel:'common',text:'Lost legacy ACK'}
  pendingChatRequest('legacy-upgrade/user/room/general',legacy,first)
  assert.equal(pendingChatRequest('legacy-upgrade/user/room/general',
    {channel_id:'new-channel',text:legacy.text},second,[legacy]).id,first)
  assert.equal(pendingChatRequest('legacy-upgrade/user/room/general',
    {channel_id:'new-channel',text:'Different message'},second,[{...legacy,text:'Different message'}]).id,second)
})
