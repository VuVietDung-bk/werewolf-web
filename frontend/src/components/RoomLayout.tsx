import { useEffect, useRef, useState, type ReactNode } from 'react'
import { type Room, type RoomMember } from '../api'
import { routeHash, workspaceFor, type RoomRoute, type View, type Workspace } from '../roomNavigation'
import ChannelList,{type RoomEntry as Entry} from './chat/ChannelList'
import MemberList from './chat/MemberList'
import { ChannelProvider, useChannelCatalog } from './chat/ChannelContext'
import { RoomRealtimeContext, useRoomRealtime } from './RoomRealtime'

type Props = {
  room: Room; userId: string; route: RoomRoute; onNavigate: (view: View, selection?: RoomRoute['selection']) => void
  onRefresh: () => void; children: ReactNode; onKick: (member: RoomMember) => void; onAccessLost:(code:number)=>void; onRealtimeRefresh:()=>void
}

export default function RoomLayout({ room, userId, route, onNavigate, onRefresh, children, onKick, onAccessLost, onRealtimeRefresh }: Props) {
  const workspace = workspaceFor(route.view)
  const realtime=useRoomRealtime(room.id,userId,onRealtimeRefresh,onAccessLost)
  const shared = useChannelCatalog(room,userId,realtime)
  const {catalog,context,error:contextError} = shared
  const [drawer, setDrawer] = useState<'channels' | 'members' | 'person' | 'menu' | null>(null)
  const [person, setPerson] = useState<RoomMember | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const lastTrigger = useRef<HTMLElement | null>(null)
  const currentHash = routeHash(route)

  useEffect(() => {
    if (drawer && !dialog.current?.open) dialog.current?.showModal()
    if (!drawer) dialog.current?.close()
  }, [drawer])
  useEffect(() => { setDrawer(null) }, [currentHash, room.id])
  const close = () => { setDrawer(null); lastTrigger.current?.focus() }
  const open = (next: NonNullable<typeof drawer>, member?: RoomMember) => {
    lastTrigger.current = document.activeElement as HTMLElement
    if (member) setPerson(member)
    setDrawer(next)
  }
  const select = (entry: Entry) => { onNavigate(entry.route.view, entry.route.selection); close() }
  const isChat = workspace === 'chat'
  const activeChat = route.selection?.channel ?? 'common'
  const defaultTeam = room.is_host ? context?.team_labels[0] : context?.current_team_label
  const active = (entry: Entry) => routeHash(entry.route) === currentHash || (
    route.view === 'chat' && route.selection?.channel === 'team' && !route.selection.team &&
    entry.route.view === 'chat' && entry.route.selection?.channel === 'team' && entry.route.selection.team === defaultTeam)
  const readOnly = room.status === 'finished'
  const hasUnread=(item:{unread:boolean})=>item.unread
  const groups: { title: string; entries: Entry[] }[] = isChat ? [
    { title: 'Đặc biệt', entries: [{ label: 'Thông báo', route: { view: 'dead' }, readonly: !room.is_host || readOnly }, { label: 'Vote', route: { view: 'vote' } }] },
    { title: 'Chung', entries: (catalog?.channels ?? []).filter((item)=>item.kind==='common').map((item):Entry=>({
      label:item.name,glyph:'common',route:{view:'chat',selection:{channel:'common'}},readonly:!item.capabilities.send,unread:hasUnread(item),
    })) },
    { title: 'Riêng', entries: [
      ...(catalog?.channels ?? []).filter((item)=>item.kind==='team').map((item):Entry=>({
        label:item.name,glyph:'team',readonly:!item.capabilities.send,unread:hasUnread(item),
        route:{view:'chat',selection:{channel:'team',team:item.team_label??undefined}},
      })),
      ...(catalog?.channels ?? []).filter((item)=>item.kind==='moderator_dm').map((item):Entry=>({
        label:item.name,glyph:'host',readonly:!item.capabilities.send,unread:hasUnread(item),
        route:{view:'chat',selection:{channel:'host',participant:room.is_host||readOnly?item.participant_user_id??undefined:undefined}},
      })),
    ] },
  ] : workspace === 'role' ? [{ title: 'Role', entries: [
    ...(!room.is_host ? [{ label: 'Role của tôi', route: { view: (room.status === 'drafting' ? 'distribution' : 'assignment') as View } }] : []),
    { label: room.is_host && ['open', 'closed'].includes(room.status) ? 'Bộ role' : 'Danh mục', route: { view: room.is_host && ['open', 'closed'].includes(room.status) ? 'manage' : 'catalog' } },
    ...(room.is_host && ['open', 'closed', 'drafting', 'assigned'].includes(room.status) ? [{ label: 'Phát role', route: { view: (room.status === 'assigned' ? 'assignment' : 'distribution') as View } }] : []),
  ] }] : [{ title: 'Quản lý', entries: [
    { label: 'Thông tin phòng', route: { view: 'lobby' } },
    ...(room.is_host && ['playing', 'finished'].includes(room.status) ? [{ label: 'Người chơi', route: { view: 'control' as const } }] : []),
    ...(room.is_host && room.status === 'playing' ? [{ label: 'Yêu cầu role', route: { view: 'requests' as const } }, { label: 'Điều hành', route: { view: 'game' as const } }] : []),
    ...(room.status === 'finished' ? [{ label: 'Kết quả & ván mới', route: { view: 'game' as const } }, { label: 'Nhật ký', route: { view: 'transcript' as const } }] : []),
  ] }]
  const selected = groups.flatMap((g) => g.entries).find(active)
  const phase = room.status === 'playing' ? `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}${room.is_paused ? ' · Tạm dừng' : ''}`
    : room.status === 'finished' ? 'Đã kết thúc · Chỉ đọc' : room.status === 'drafting' ? 'Đang chọn role' : room.status === 'assigned' ? 'Đã phát role' : 'Sảnh chờ'
  function tabDefault(tab: Workspace): View {
    return tab === 'chat' ? 'chat' : tab === 'role' ? room.is_host ? ['open', 'closed'].includes(room.status) ? 'manage' : 'catalog' : room.status === 'drafting' ? 'distribution' : 'assignment'
      : room.is_host && room.status === 'playing' ? 'control' : 'lobby'
  }
  const channelList=<ChannelList groups={groups} active={active} onSelect={select} error={isChat?contextError:undefined}/>
  const memberList=<MemberList room={room} onSelect={member=>open('person',member)}/>


  return <RoomRealtimeContext.Provider value={realtime}><ChannelProvider value={shared}><div className={`room-layout room-layout-${workspace}`}>
    <header className="room-topbar">
      <button className="room-name-menu" type="button" onClick={() => open('menu')} aria-label={`Menu phòng ${room.name}`}><span aria-hidden="true">☾</span><span>{room.name} ▾</span></button>
      <nav aria-label="Điều hướng phòng">{(['chat', 'role', 'management'] as Workspace[]).map((tab) => <button key={tab} type="button" className={workspace === tab ? 'active' : ''}
        aria-current={workspace === tab ? 'page' : undefined} onClick={() => onNavigate(tabDefault(tab))}>{tab === 'chat' ? 'Chat' : tab === 'role' ? 'Role' : 'Quản lý'}</button>)}</nav>
      <span className="room-phase">{phase}</span>
      <small className="room-live-state" data-state={realtime.state} role="status">
        {realtime.state==='live'?'Trực tiếp':realtime.state==='connecting'?'Đang kết nối…':'Đang tự cập nhật'}
      </small>
    </header>
    <div className="room-workspace">
      <aside className="room-sidebar" aria-label={isChat ? 'Danh sách kênh' : 'Tác vụ'}>{channelList}</aside>
      <section className="room-main">
        <header className="room-channel-header">
          <button type="button" className="room-open-channels" aria-label="Mở danh sách kênh hoặc tác vụ" onClick={() => open('channels')}>☰</button>
          <h1>{selected?.label ?? (route.view === 'chat' && activeChat === 'host' ? 'Quản trò' : 'Phòng chơi')}</h1>
          <small className="room-channel-phase">{phase}</small>
          {isChat && <button type="button" className="room-open-members" aria-label="Mở danh sách người chơi" onClick={() => open('members')}>♙</button>}
        </header>
        <div className={`room-view ${route.view === 'chat' ? 'room-view-chat' : ''}`}>{children}</div>
      </section>
      {isChat && <aside className="room-member-sidebar" aria-label="Người chơi trong phòng">{memberList}</aside>}
    </div>
    <dialog ref={dialog} className={`room-drawer ${drawer === 'menu' ? 'room-menu-dialog' : ''}`} aria-labelledby="room-drawer-title"
      onCancel={(event) => { event.preventDefault(); close() }} onClose={() => setDrawer(null)}
      onKeyDown={(event) => {
        if (event.key !== 'Tab') return
        const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
        )).filter((element) => element.getClientRects().length > 0)
        const first = controls[0]
        const last = controls.at(-1)
        if (!first || !last) return
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }}>
      <header><h2 id="room-drawer-title">{drawer === 'channels' ? isChat ? 'Kênh chat' : 'Tác vụ' : drawer === 'members' ? 'Người chơi' : drawer === 'person' ? person?.display_name : room.name}</h2>
        <button type="button" onClick={close} aria-label="Đóng">✕</button></header>
      <div className="room-drawer-body">{drawer === 'channels' ? channelList : drawer === 'members' ? memberList : drawer === 'person' && person ? <>
        <p>Ghế {person.seat_number} · {person.public_is_alive ? 'Đang sống công khai' : 'Đã công bố chết'}</p>
        <p>Role: {person.public_role_name ?? 'Chưa công bố'}</p>
        {room.is_host && room.status === 'playing' && <button type="button" onClick={() => { onNavigate('control'); close() }}>Mở bàn quản trò</button>}
        {room.is_host && ['open', 'closed', 'drafting', 'assigned', 'playing'].includes(room.status) && <button type="button" onClick={() => { close(); onKick(person) }}>Kick người chơi…</button>}
      </> : drawer === 'menu' ? <div className="room-menu-actions">
        <p>Mã mời: <strong>{room.code}</strong></p><p>{room.members.length}/{room.seat_limit} ghế · {phase}</p>
        <button type="button" onClick={() => { onNavigate('home'); close() }}>Về trang vào làng</button>
        <button type="button" onClick={() => { onNavigate('lobby'); close() }}>Thông tin phòng / rời phòng</button>
        {room.status === 'finished' && <button type="button" onClick={() => { onNavigate('transcript'); close() }}>Nhật ký sau trận</button>}
        <button type="button" onClick={()=>{onRefresh();void shared.refresh()}}>Cập nhật phòng</button>
      </div> : null}</div>
    </dialog>
  </div></ChannelProvider></RoomRealtimeContext.Provider>
}
