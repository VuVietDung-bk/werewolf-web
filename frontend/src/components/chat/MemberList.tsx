import type { Room,RoomMember } from '../../api'
import StatusGlyph from '../StatusGlyph'
export default function MemberList({room,onSelect}:{room:Room;onSelect:(member:RoomMember)=>void}){
  return <>
    <h2>Quản trò</h2><div className="room-member"><span className="room-avatar" aria-hidden="true">☾</span><span>{room.host_name}<small>Quản trò</small></span></div>
    {[true, false].map((alive) => <div key={String(alive)}><h2>{alive ? 'Đang sống' : 'Đã công bố chết'} — {room.members.filter((m) => m.public_is_alive === alive).length}</h2>
      {room.members.filter((m) => m.public_is_alive === alive).map((member) => <button className="room-member" key={member.user_id} type="button" onClick={() => onSelect(member)}>
        <span className="room-avatar" aria-hidden="true">{alive ? member.display_name.slice(0, 1).toUpperCase() : <StatusGlyph kind="dead" size={20} />}</span>
        <span>{member.display_name}<small>Ghế {member.seat_number}{member.public_role_name ? ` · ${member.public_role_name}` : ''}</small></span>
      </button>)}
    </div>)}
  </>
}
