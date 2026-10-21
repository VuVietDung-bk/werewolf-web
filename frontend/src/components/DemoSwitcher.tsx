import { useEffect, useState } from 'react'
import { api, jsonBody, type Identity } from '../api'

type DemoActor = { id: string; name: string; kind: 'host' | 'player'; seat: number | null }
type DemoInfo = { room_id: string; room_name: string; actors: DemoActor[] }
const KEY = 'ma-soi-local-demo-key'

const imported = window.location.hash.match(/^#demo=([A-Za-z0-9_-]+)$/)
if (imported) {
  window.sessionStorage.setItem(KEY, imported[1])
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#home`)
}

export default function DemoSwitcher({ identity }: { identity: Identity | null }) {
  const [key, setKey] = useState(() => window.sessionStorage.getItem(KEY) ?? '')
  const [info, setInfo] = useState<DemoInfo | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!key) return
    let active = true
    void api<DemoInfo>('/api/dev/demo', { headers: { 'X-Demo-Key': key } })
      .then((value) => { if (active) { setInfo(value); setError('') } })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : 'Không mở được phòng thử.') })
    return () => { active = false }
  }, [key])

  if (!key) return null
  const selected = selectedId || (info?.actors.some((actor) => actor.id === identity?.id)
    ? identity!.id : info?.actors[0]?.id ?? '')

  async function switchActor() {
    if (!selected || !info || busy) return
    setBusy(true)
    setError('')
    try {
      await api('/api/dev/demo/switch', { method: 'POST',
        headers: { 'X-Demo-Key': key }, body: jsonBody({ actor_id: selected }) })
      window.localStorage.setItem('ma-soi-active-room', info.room_id)
      window.location.reload()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không chuyển được tài khoản.')
      setBusy(false)
    }
  }

  return <section className="demo-switcher" aria-label="Tài khoản trải nghiệm local">
    <div><span className="eyebrow">TRẢI NGHIỆM LOCAL</span>
      <strong>{info?.room_name ?? 'Phòng mẫu'}</strong>
      <small>Đổi góc nhìn để tự thử các thao tác. Các tài khoản khác không tự hành động.</small></div>
    {info && <div className="demo-switcher-controls">
      <label htmlFor="demo-actor">Vào vai</label>
      <select id="demo-actor" value={selected} disabled={busy}
        onChange={(event) => setSelectedId(event.target.value)}>
        {info.actors.map((actor) => <option key={actor.id} value={actor.id}>
          {actor.kind === 'host' ? 'Quản trò' : `Ghế ${actor.seat}`} · {actor.name}
        </option>)}
      </select>
      <button type="button" disabled={busy || selected === identity?.id} onClick={() => void switchActor()}>
        Chuyển tài khoản
      </button>
    </div>}
    <button type="button" className="button-secondary demo-hide" onClick={() => {
      window.sessionStorage.removeItem(KEY)
      setKey('')
    }}>Ẩn</button>
    {error && <p className="form-message" role="alert">{error}</p>}
  </section>
}
