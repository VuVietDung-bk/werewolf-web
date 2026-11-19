import type { RoomSummary } from './api'

export type RoomSeries = { latest: RoomSummary; history: RoomSummary[] }

export function latestAccessibleSuccessor(room: RoomSummary, rooms: RoomSummary[]): RoomSummary | null {
  const byId = new Map(rooms.map((item) => [item.id, item]))
  const visited = new Set([room.id])
  let nextId = room.next_room_id
  let latest: RoomSummary | null = null
  while (nextId && !visited.has(nextId)) {
    const next = byId.get(nextId)
    if (!next) break
    latest = next
    visited.add(nextId)
    nextId = next.next_room_id
  }
  return latest
}

export function groupRoomSeries(rooms: RoomSummary[]): RoomSeries[] {
  const grouped = new Map<string, RoomSummary[]>()
  for (const item of rooms) {
    const latest = latestAccessibleSuccessor(item, rooms) ?? item
    const entries = grouped.get(latest.id) ?? []
    entries.push(item)
    grouped.set(latest.id, entries)
  }
  const byId = new Map(rooms.map((item) => [item.id, item]))
  return [...grouped.entries()]
    .sort(([left], [right]) => rooms.findIndex((item) => item.id === left)
      - rooms.findIndex((item) => item.id === right))
    .map(([latestId, entries]) => ({
      latest: byId.get(latestId)!,
      history: entries.filter((item) => item.id !== latestId).reverse(),
    }))
}
