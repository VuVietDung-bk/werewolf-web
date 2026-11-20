import type { ChatChannel, Room } from './api'

// Legacy screen keys stay internal while their components move into workspaces.
export type View = 'home' | 'lobby' | 'catalog' | 'manage' | 'distribution' | 'assignment' | 'game' | 'chat' | 'requests' | 'vote' | 'control' | 'dead' | 'transcript'
export type Workspace = 'chat' | 'role' | 'management'
export type ChatSelection = { channel: ChatChannel; team?: string; participant?: string }
export type RoomRoute = { view: View; selection?: ChatSelection }
const legacy: View[] = ['home', 'lobby', 'catalog', 'manage', 'distribution', 'assignment', 'game', 'chat', 'requests', 'vote', 'control', 'dead', 'transcript']
export const workspaceFor = (view: View): Workspace => ['chat', 'dead', 'vote'].includes(view) ? 'chat'
  : ['catalog', 'manage', 'distribution', 'assignment'].includes(view) ? 'role' : 'management'

const lookup = (values: Record<string, View>, key: string, fallback: View): View => Object.hasOwn(values, key) ? values[key] : fallback

export function readRoomRoute(): RoomRoute {
  const raw = window.location.hash.slice(1)
  if (raw === 'private') return { view: 'chat', selection: { channel: 'host' } }
  if (raw === 'game') return { view: 'chat' }
  if (legacy.includes(raw as View)) return { view: raw as View }
  const [tab, page, encoded] = raw.split('/')
  let id: string | undefined
  try { id = encoded ? decodeURIComponent(encoded) : undefined } catch { return { view: 'chat' } }
  if (tab === 'chat') {
    if (page === 'announcements') return { view: 'dead' }
    if (page === 'vote') return { view: 'vote' }
    if (page === 'team') return { view: 'chat', selection: { channel: 'team', team: id } }
    if (page === 'dm') return { view: 'chat', selection: { channel: 'host', participant: id } }
    if (page === 'requests') return { view: 'requests' }
    return { view: 'chat', selection: { channel: 'common' } }
  }
  if (tab === 'role') return { view: lookup({ catalog: 'catalog', edit: 'manage', distribution: 'distribution', mine: 'assignment' }, page, 'catalog') }
  if (tab === 'management') return { view: lookup({ room: 'lobby', game: 'game', players: 'control', requests: 'requests', history: 'transcript' }, page, 'lobby') }
  return { view: 'home' }
}

export function routeHash(route: RoomRoute): string {
  const { view, selection } = route
  const encode = (value?: string) => value ? `/${encodeURIComponent(value)}` : ''
  if (view === 'chat') return selection?.channel === 'team' ? `#chat/team${encode(selection.team)}`
    : selection?.channel === 'host' ? `#chat/dm${encode(selection.participant)}` : '#chat/general'
  return ({ home: '#home', lobby: '#management/room', game: '#management/game', catalog: '#role/catalog',
    manage: '#role/edit', distribution: '#role/distribution', assignment: '#role/mine',
    requests: '#management/requests', vote: '#chat/vote', control: '#management/players', dead: '#chat/announcements',
    transcript: '#management/history' } as Record<View, string>)[view]
}

export function normalizeRoute(route: RoomRoute, room: Room): RoomRoute {
  const playing = ['playing', 'finished'].includes(room.status)
  if (route.view === 'home') return route
  if (route.view === 'requests' && !room.is_host) return { view: 'chat', selection: { channel: 'host' } }
  if (route.view === 'control' && !room.is_host) return { view: 'lobby' }
  if (route.view === 'assignment' && room.is_host && playing) return { view: 'control' }
  if (route.view === 'distribution' && room.status === 'assigned') return { view: 'assignment' }
  if (['manage', 'distribution'].includes(route.view) && !['open', 'closed', 'drafting'].includes(room.status)) return { view: 'catalog' }
  if (route.view === 'manage' && (!room.is_host || room.status === 'drafting')) return { view: 'catalog' }
  if (route.view === 'assignment' && !room.is_host && room.status === 'drafting') return { view: 'distribution' }
  if (route.view === 'distribution' && !room.is_host && room.status !== 'drafting') return { view: 'assignment' }
  if (route.view === 'control' && !playing) return { view: 'lobby' }
  if (route.view === 'transcript' && room.status !== 'finished') return { view: 'game' }
  if (route.view === 'chat' && route.selection?.participant && !room.is_host && room.status !== 'finished') return { view: 'chat', selection: { channel: 'host' } }
  return route
}
