import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/roomNavigation.ts', import.meta.url), 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const sandbox = { exports: {}, window: { location: { hash: '' } } }
vm.runInNewContext(js, sandbox)
const { readRoomRoute, normalizeRoute, routeHash, workspaceFor } = sandbox.exports
const room = { is_host: true, status: 'playing' }
const read = hash => { sandbox.window.location.hash = hash; return readRoomRoute() }
const plain = value => JSON.parse(JSON.stringify(value))

test('legacy links map into three workspaces', () => {
  assert.equal(routeHash(read('#dead')), '#chat/announcements')
  assert.equal(routeHash(read('#vote')), '#chat/vote')
  assert.equal(routeHash(read('#game')), '#chat/general')
  assert.equal(routeHash(read('#private')), '#chat/dm')
  assert.equal(workspaceFor(read('#control').view), 'management')
})
test('team and DM identifiers round trip without breaking routes', () => {
  for (const selection of [{channel:'team',team:'Bầy sói / đêm #1'},{channel:'host',participant:'1234'}]) {
    const route = {view:'chat',selection}
    assert.deepEqual(plain(read(routeHash(route))), route)
  }
})
test('malformed encoding falls back to a safe channel', () => {
  assert.equal(routeHash(read('#chat/team/%E0%A4%A')), '#chat/general')
})
test('players cannot open host screens or another active private conversation', () => {
  const player = { ...room, is_host: false }
  assert.equal(normalizeRoute(read('#management/players'), player).view, 'lobby')
  assert.equal(normalizeRoute(read('#role/edit'), player).view, 'catalog')
  assert.deepEqual(plain(normalizeRoute(read('#chat/dm/other'), player)), {view:'chat',selection:{channel:'host'}})
})
test('a player in the draft keeps a working role selection screen', () => {
  assert.equal(normalizeRoute(read('#role/mine'), {status:'drafting',is_host:false}).view, 'distribution')
  assert.equal(normalizeRoute(read('#role/distribution'), {status:'drafting',is_host:false}).view, 'distribution')
})
test('host assignments become board during the game, history waits for finish', () => {
  assert.equal(normalizeRoute(read('#role/distribution'), {...room,status:'assigned'}).view, 'assignment')
  assert.equal(normalizeRoute(read('#role/mine'), room).view, 'control')
  assert.equal(normalizeRoute(read('#transcript'), room).view, 'game')
  assert.equal(normalizeRoute(read('#transcript'), {...room,status:'finished'}).view, 'transcript')
})
test('normalization converges for every lifecycle and role', () => {
  for (const is_host of [true,false]) for (const status of ['open','closed','drafting','assigned','playing','finished']) {
    for (const hash of ['#lobby','#catalog','#manage','#distribution','#assignment','#requests','#control','#transcript','#chat/dm/other']) {
      let route = read(hash), settled = false
      const visited = new Set()
      for (let i=0;i<5;i++) {
        const next = normalizeRoute(route,{is_host,status})
        if (routeHash(next)===routeHash(route)) {settled=true;break}
        assert.ok(!visited.has(routeHash(next)),`${status}/${is_host}/${hash} loops`)
        visited.add(routeHash(next));route=next
      }
      assert.ok(settled,`${status}/${is_host}/${hash} does not converge`)
    }
  }
})
