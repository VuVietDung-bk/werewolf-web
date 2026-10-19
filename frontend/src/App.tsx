import { useEffect, useState, type FormEvent } from 'react'
import { api, jsonBody, type Identity, type Room, type RoomSummary } from './api'
import PixelAsset from './components/PixelAsset'
import RolePanel from './components/RolePanel'
import GameControls from './components/GameControls'
import HostPlayerBoard from './components/HostPlayerBoard'

type ApiStatus = 'loading' | 'online' | 'offline'
type View = 'home' | 'lobby' | 'catalog' | 'manage' | 'distribution' | 'assignment' | 'game' | 'control'
const VIEWS: View[] = ['home', 'lobby', 'catalog', 'manage', 'distribution', 'assignment', 'game', 'control']
const ROOM_KEY = 'ma-soi-active-room'
const showAssetDemo = new URLSearchParams(window.location.search).has('asset-demo')
const readView = (): View => {
  const value = window.location.hash.slice(1)
  return VIEWS.includes(value as View) ? value as View : 'home'
}
const roomViews = (room: Room): View[] => [
  ...(['playing', 'finished'].includes(room.status) ? ['game'] as View[] : ['lobby'] as View[]),
  'catalog',
  ...(['open', 'closed'].includes(room.status) && room.is_host ? ['manage'] as View[] : []),
  ...((['open', 'closed'].includes(room.status) && room.is_host) || room.status === 'drafting'
    ? ['distribution'] as View[] : []),
  ...(['assigned', 'playing', 'finished'].includes(room.status) ? ['assignment'] as View[] : []),
  ...(['playing', 'finished'].includes(room.status) && room.is_host ? ['control'] as View[] : []),
]
const roomDefaultView = (room: Room): View => ['playing', 'finished'].includes(room.status) ? 'game'
  : room.status === 'assigned' ? 'assignment' : room.status === 'drafting' ? 'distribution' : 'lobby'

export default function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>('loading')
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [rooms, setRooms] = useState<RoomSummary[]>([])
  const [room, setRoom] = useState<Room | null>(null)
  const [guestName, setGuestName] = useState('')
  const [newRoomName, setNewRoomName] = useState('')
  const [newRoomSeats, setNewRoomSeats] = useState(12)
  const [joinCode, setJoinCode] = useState('')
  const [consented, setConsented] = useState(false)
  const [settingsName, setSettingsName] = useState('')
  const [settingsSeats, setSettingsSeats] = useState(12)
  const [registrationOpen, setRegistrationOpen] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [view, setView] = useState<View>(readView)
  const [ready, setReady] = useState(false)

  function navigate(next: View, replace = false) {
    const hash = `#${next}`
    if (replace) window.history.replaceState(null, '', hash)
    else if (window.location.hash !== hash) window.location.hash = hash
    setView(next)
    window.scrollTo({ top: 0, behavior: 'instant' })
  }

  async function loadRooms(preferredId?: string): Promise<Room | null> {
    const list = await api<RoomSummary[]>('/api/rooms/mine')
    setRooms(list)
    const remembered = preferredId ?? localStorage.getItem(ROOM_KEY)
    const selected = list.find((item) => item.id === remembered) ?? list[0]
    if (!selected) {
      setRoom(null)
      localStorage.removeItem(ROOM_KEY)
      return null
    }
    const detail = await api<Room>(`/api/rooms/${selected.id}`)
    setRoom(detail)
    localStorage.setItem(ROOM_KEY, selected.id)
    return detail
  }

  useEffect(() => {
    const syncView = () => setView(readView())
    window.addEventListener('hashchange', syncView)
    return () => window.removeEventListener('hashchange', syncView)
  }, [])

  useEffect(() => {
    let alive = true
    async function restore() {
      try {
        const response = await fetch('/api/health/live')
        if (alive) setApiStatus(response.ok ? 'online' : 'offline')
      } catch {
        if (alive) setApiStatus('offline')
      }
      let user: Identity
      try { user = await api<Identity>('/api/auth/me') }
      catch { if (alive) setReady(true); return } // A missing or expired cookie is normal before guest login.
      if (!alive) return
      setIdentity(user)
      try { await loadRooms() }
      catch (error) {
        if (alive) setMessage(error instanceof Error ? error.message : 'Không tải được danh sách phòng.')
      }
      if (alive) setReady(true)
    }
    void restore()
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (!ready || view === 'home') return
    if (!room) { navigate('home', true); return }
    if (!roomViews(room).includes(view)) navigate(roomDefaultView(room), true)
  }, [ready, room?.id, room?.status, room?.is_host, view])

  useEffect(() => {
    if (!room) return
    setSettingsName(room.name)
    setSettingsSeats(room.seat_limit)
    setRegistrationOpen(room.status === 'open')
  }, [room?.id])

  useEffect(() => {
    if (!room) return
    const roomId = room.id
    let active = true
    const timer = window.setInterval(() => {
      void api<Room>(`/api/rooms/${roomId}`).then((latest) => {
        if (!active) return
        setRoom((current) => current?.id === roomId && latest.version > current.version ? latest : current)
        setRooms((current) => current.map((item) => item.id === roomId && latest.version > (room?.version ?? 0)
          ? { ...item, name: latest.name, status: latest.status, phase_type: latest.phase_type,
            round_number: latest.round_number, is_paused: latest.is_paused,
            result_label: latest.result_label, result_reason: latest.result_reason,
            ended_at: latest.ended_at } : item))
      }).catch(() => {})
    }, 8000)
    return () => { active = false; window.clearInterval(timer) }
  }, [room?.id, room?.version])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setMessage('')
    try { await action() }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Có lỗi kết nối.') }
    finally { setBusy(false) }
  }

  function enter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void run(async () => {
      const user = await api<Identity>('/api/auth/guest', {
        method: 'POST', body: jsonBody({ display_name: guestName.trim() }),
      })
      setIdentity(user)
      await loadRooms()
    })
  }

  function leaveAccount() {
    void run(async () => {
      await api<void>('/api/auth/logout', { method: 'POST' })
      setIdentity(null)
      setRooms([])
      setRoom(null)
      localStorage.removeItem(ROOM_KEY)
      navigate('home')
    })
  }

  function createRoom(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void run(async () => {
      const created = await api<Room>('/api/rooms', {
        method: 'POST', body: jsonBody({ name: newRoomName.trim(), seat_limit: newRoomSeats }),
      })
      setNewRoomName('')
      await loadRooms(created.id)
      navigate('lobby')
    })
  }

  function joinRoom(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void run(async () => {
      const joined = await api<Room>('/api/rooms/join', {
        method: 'POST', body: jsonBody({ code: joinCode.trim(), accept_postgame_transcript: consented }),
      })
      setJoinCode('')
      setConsented(false)
      await loadRooms(joined.id)
      navigate('lobby')
    })
  }

  function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!room) return
    void run(async () => {
      const updated = await api<Room>(`/api/rooms/${room.id}/settings`, {
        method: 'PATCH', body: jsonBody({ name: settingsName.trim(), seat_limit: settingsSeats,
          registration_open: registrationOpen }),
      })
      setRoom(updated)
      await loadRooms(updated.id)
    })
  }

  function leaveRoom() {
    if (!room) return
    void run(async () => {
      await api<void>(`/api/rooms/${room.id}/leave`, { method: 'POST' })
      await loadRooms()
      navigate('home')
    })
  }

  function refreshRoom() {
    if (!room) return
    void run(async () => { await loadRooms(room.id) })
  }

  function startGame() {
    if (!room) return
    void run(async () => {
      await api<Room>(`/api/rooms/${room.id}/game/start`, { method: 'POST' })
      await loadRooms(room.id)
      navigate('game')
    })
  }

  function selectRoom(id: string) {
    void run(async () => {
      const selected = await loadRooms(id)
      if (selected) navigate(roomDefaultView(selected))
    })
  }

  const seats = room ? Array.from({ length: room.seat_limit }, (_, index) => {
    const number = index + 1
    return { number, member: room.members.find((item) => item.seat_number === number) }
  }) : []
  const tabs: { view: View; label: string }[] = [
    { view: 'home', label: 'Trang chủ' },
    ...(room ? roomViews(room).map((item) => ({ view: item, label: {
      lobby: 'Sảnh chờ', catalog: 'Danh mục role', manage: 'Thiết lập role', distribution: room.status === 'drafting' ? 'Chọn role' : 'Phát role',
      assignment: room.is_host ? 'Phân role' : 'Role của tôi', game: 'Trận đấu', control: 'Bàn quản trò', home: 'Trang chủ',
    }[item] })) : []),
  ]

  return <main className="shell">
    <header className="masthead">
      <span className="brand">MA SÓI · HỒ SƠ LÀNG</span>
      <span className="edition">Bản thử nghiệm · Ma Sói do quản trò điều hành</span>
    </header>

    <nav className="screen-nav" aria-label="Điều hướng màn hình">
      <span className="screen-nav-context">{room?.name ?? 'Ma Sói'}</span>
      {tabs.map((tab) => <button key={tab.view} type="button" className={view === tab.view ? 'active' : ''}
        aria-current={view === tab.view ? 'page' : undefined} onClick={() => navigate(tab.view)}>{tab.label}</button>)}
    </nav>

    {view === 'home' && <>
    <section className="hero" aria-labelledby="page-title">
      <div className="hero-copy">
        <span className="eyebrow">MỘT VÁN CHƠI DO QUẢN TRÒ DẪN DẮT</span>
        <h1 id="page-title">Câu chuyện của ngôi làng bắt đầu ở đây.</h1>
        <p>Tạo sảnh, mời người chơi và chuẩn bị cho đêm đầu tiên.</p>
        <div className="status" role="status">
          <span className={`status-dot ${apiStatus}`} aria-hidden="true" />
          API: {apiStatus === 'loading' ? 'đang kiểm tra' : apiStatus === 'online' ? 'đang hoạt động' : 'chưa kết nối'}
        </div>
      </div>
    </section>

    <section className="identity-panel" aria-labelledby="identity-title">
      <div>
        <span className="eyebrow">DANH TÍNH</span>
        <h2 id="identity-title">{identity ? `Chào ${identity.display_name}` : 'Vào làng bằng phiên khách'}</h2>
        <p>{identity ? 'Phiên của bạn được khôi phục khi tải lại trang.' : 'Nhập tên hiển thị để tạo phòng hoặc nhận lời mời.'}</p>
      </div>
      {identity ? <button type="button" className="button-secondary" onClick={leaveAccount} disabled={busy}>Đăng xuất</button> :
        <form onSubmit={enter} className="stack-form">
          <label htmlFor="guest-name">Tên hiển thị</label>
          <input id="guest-name" value={guestName} maxLength={80} required onChange={(event) => setGuestName(event.target.value)} />
          <button disabled={busy || !guestName.trim()}>Tiếp tục</button>
        </form>}
    </section>

    {identity && <>
      <section className="room-actions" aria-label="Tạo hoặc vào phòng">
        <form className="panel stack-form" onSubmit={createRoom}>
          <span className="eyebrow">QUẢN TRÒ</span><h2>Tạo phòng</h2>
          <label htmlFor="room-name">Tên phòng</label>
          <input id="room-name" value={newRoomName} maxLength={120} required
            onChange={(event) => setNewRoomName(event.target.value)} placeholder="Ví dụ: Thôn Sương" />
          <label htmlFor="room-seats">Số ghế người chơi</label>
          <input id="room-seats" type="number" min={2} max={30} value={newRoomSeats}
            onChange={(event) => setNewRoomSeats(Number(event.target.value))} />
          <button disabled={busy || !newRoomName.trim()}>Tạo sảnh</button>
        </form>
        <form className="panel stack-form" onSubmit={joinRoom}>
          <span className="eyebrow">NGƯỜI CHƠI</span><h2>Vào phòng</h2>
          <label htmlFor="join-code">Mã mời</label>
          <input id="join-code" value={joinCode} minLength={8} maxLength={16} required
            onChange={(event) => setJoinCode(event.target.value.toUpperCase())} placeholder="12 ký tự" />
          <label className="consent"><input type="checkbox" checked={consented}
            onChange={(event) => setConsented(event.target.checked)} />
            <span>Tôi đồng ý rằng sau trận, người từng tham gia phòng được xem toàn bộ chat đội, chat với quản trò, yêu cầu role, ghi chú và quyết định của quản trò.</span>
          </label>
          <button disabled={busy || !consented || joinCode.trim().length < 8}>Vào sảnh</button>
        </form>
      </section>

      {rooms.length > 0 && <nav className="room-switcher" aria-label="Phòng của tôi">
        <span>Phòng của tôi</span>
        {rooms.map((item) => <button key={item.id} type="button" disabled={busy}
          className={room?.id === item.id ? 'room-tab active' : 'room-tab'}
          onClick={() => selectRoom(item.id)}>{item.name}</button>)}
      </nav>}

    </>}
    </>}

    {room && view !== 'home' && <>
      <header className="room-context">
        <div><span className="eyebrow">{room.status === 'finished' ? 'TRẬN ĐÃ KẾT THÚC' : room.status === 'playing' ? `${room.phase_type === 'night' ? 'ĐÊM' : 'NGÀY'} ${room.round_number}${room.is_paused ? ' · TẠM DỪNG' : ''}` : room.status === 'assigned' ? 'ROLE ĐÃ ĐƯỢC PHÁT' : room.status === 'drafting' ? 'ĐANG CHỌN ROLE' : room.status === 'open' ? 'ĐANG NHẬN NGƯỜI CHƠI' : 'ĐÃ ĐÓNG ĐĂNG KÝ'}</span>
          <h1>{room.name}</h1><p>Quản trò: <strong>{room.host_name}</strong> · {room.members.length}/{room.seat_limit} ghế</p></div>
        <button type="button" className="button-secondary" onClick={refreshRoom} disabled={busy}>Cập nhật phòng</button>
      </header>
      {view === 'lobby' && <section className="lobby" aria-labelledby="lobby-title">
        <h2 id="lobby-title">Sảnh chờ</h2>
        <div className="invite-line"><span>Mã mời</span><strong>{room.code}</strong>
          <span>Chia sẻ mã này cho người chơi. Quản trò không chiếm ghế.</span></div>
        <ol className="seat-grid" aria-label="Danh sách ghế">
          {seats.map(({ number, member }) => <li key={number} className={member ? 'seat occupied' : 'seat empty'}>
            <span className="seat-avatar" aria-hidden="true">{member ? '◆' : '·'}</span>
            <span className="seat-text"><small>GHẾ {number.toString().padStart(2, '0')}</small>
              <strong>{member?.display_name ?? 'Đang chờ'}</strong></span>
          </li>)}
        </ol>
        {room.is_host && room.status === 'assigned' && <div className="start-game-box">
          <p>Mọi người đã có role.</p>
          <button type="button" onClick={() => navigate('assignment')}>Xem phân role và bắt đầu</button>
        </div>}
        {room.is_host && ['open', 'closed'].includes(room.status) ? <form className="host-settings stack-form" onSubmit={saveSettings}>
          <div><span className="eyebrow">CÀI ĐẶT QUẢN TRÒ</span><h3>Điều chỉnh sảnh</h3></div>
          <label htmlFor="settings-name">Tên phòng</label>
          <input id="settings-name" value={settingsName} maxLength={120} required
            onChange={(event) => setSettingsName(event.target.value)} />
          <label htmlFor="settings-seats">Số ghế</label>
          <input id="settings-seats" type="number" min={2} max={30} value={settingsSeats}
            onChange={(event) => setSettingsSeats(Number(event.target.value))} />
          <label className="consent"><input type="checkbox" checked={registrationOpen}
            onChange={(event) => setRegistrationOpen(event.target.checked)} />
            <span>Tiếp tục nhận người chơi</span></label>
          <button disabled={busy}>Lưu cài đặt</button>
        </form> : !room.is_host && ['open', 'closed'].includes(room.status) ? <button type="button" className="button-secondary leave-room" onClick={leaveRoom} disabled={busy}>Rời phòng</button> : null}
      </section>}
      {view === 'assignment' && room.is_host && room.status === 'assigned' && <div className="start-game-box">
        <p>Mọi người đã có role. Kiểm tra phân role rồi bắt đầu ngày 0.</p>
        <button type="button" disabled={busy} onClick={startGame}>Bắt đầu ngày 0</button>
      </div>}
      {(['catalog', 'manage', 'distribution', 'assignment'] as View[]).includes(view) &&
        <RolePanel key={room.id} view={view as 'catalog' | 'manage' | 'distribution' | 'assignment'} room={room}
          onRoomChanged={async () => { await loadRooms(room.id) }} />}
      {view === 'game' && <section className="game-screen" aria-labelledby="game-title">
        <span className="eyebrow">{room.status === 'finished' ? 'KẾT QUẢ TRẬN' : 'TRẬN ĐẤU'}</span>
        <h2 id="game-title">{room.phase_type === 'night' ? 'Đêm' : 'Ngày'} {room.round_number}</h2>
        {room.status === 'finished' ? <div className="game-result"><strong>{room.result_label}</strong><p>{room.result_reason}</p></div> :
          room.is_paused ? <p className="game-paused" role="status">Trận đang tạm dừng. Vui lòng chờ quản trò tiếp tục.</p> :
            <p>Ván đang diễn ra. Dùng thanh điều hướng để xem role của bạn và danh mục role.</p>}
        <GameControls room={room} onRoomChanged={async () => { await loadRooms(room.id) }} />
        <h3>Người trong trận</h3>
        <ol className="seat-grid" aria-label="Người trong trận">{room.members.map((member) => <li key={member.user_id} className="seat occupied">
          <span className="seat-avatar" aria-hidden="true">◆</span><span className="seat-text"><small>GHẾ {member.seat_number.toString().padStart(2, '0')}</small>
            <strong>{member.display_name}</strong></span></li>)}</ol>
      </section>}
      {view === 'control' && room.is_host && <HostPlayerBoard key={room.id} room={room} />}
    </>}

    {message && <p className="form-message global-message" role="alert">{message}</p>}
    {showAssetDemo && <details className="asset-demo" open><summary>Thử asset và ảnh dự phòng</summary>
      <div className="asset-grid">
        <article className="asset-card"><PixelAsset src="/assets/v1/seer-concept.png" alt="Chân dung Tiên Tri" baseWidth={64} baseHeight={64} scale={3} />
          <h3>PNG · bản concept</h3><p>Khung 64 × 64, nhân 3 lần.</p></article>
        <article className="asset-card"><PixelAsset src="/assets/v1/missing-icon.webp" alt="Icon role mặc định" baseWidth={64} baseHeight={64} scale={3} />
          <h3>Ảnh thiếu</h3><p>Nhãn vẫn hiện khi file lỗi hoặc vắng mặt.</p></article>
      </div>
    </details>}
    <p className="footnote">Chat, công bố trạng thái và các thao tác quản trò khác sẽ được thêm ở các bước tiếp theo.</p>
  </main>
}
