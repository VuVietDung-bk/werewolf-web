import type { RoomRoute } from '../../roomNavigation'
import { routeHash } from '../../roomNavigation'
import ChatGlyph from '../ChatGlyph'
export type RoomEntry={label:string;route:RoomRoute;glyph?:'common'|'team'|'host';readonly?:boolean;unread?:boolean}
export default function ChannelList({groups,active,onSelect,error}:{groups:{title:string;entries:RoomEntry[]}[];active:(entry:RoomEntry)=>boolean;onSelect:(entry:RoomEntry)=>void;error?:string}){
  return <>{groups.map((group) => <div className="room-channel-group" key={group.title}>
    <h2>{group.title}</h2>{group.entries.map((entry) => <button key={routeHash(entry.route)} type="button" className={active(entry) ? 'active' : ''}
      aria-current={active(entry) ? 'page' : undefined} onClick={() => onSelect(entry)}>
      {entry.glyph ? <ChatGlyph channel={entry.glyph} /> : <span aria-hidden="true">{entry.route.view === 'vote' ? '☑' : entry.route.view === 'dead' ? '◉' : '◇'}</span>}
      <span className="room-entry-name">{entry.label}</span>{entry.unread && <small className="room-unread" aria-label="Có tin mới">Mới</small>}{entry.readonly && <small className="room-readonly" aria-label="Chỉ đọc">Đọc</small>}
    </button>)}</div>)}{error && <p className="room-channel-error" role="alert">{error}</p>}</>
}
