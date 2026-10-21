import { useEffect, useState, type FormEvent } from 'react'
import { api, jsonBody, type PrivateMessage, type Room } from '../api'
import { useRequestIds } from '../useRequestIds'

function MessageList({ messages }: { messages: PrivateMessage[] }) {
  return messages.length ? <ol className="private-message-list">{messages.map((message) =>
    <li key={message.id}><small><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleString('vi-VN')}</time>
      {message.edited_at && ' · Đã sửa'}</small>
      <p>{message.text}</p></li>)}</ol> : <p>Chưa có tin riêng từ quản trò.</p>
}

export default function PrivateInbox({ room }: { room: Room }) {
  const [messages, setMessages] = useState<PrivateMessage[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    const load = () => { void api<PrivateMessage[]>(`/api/rooms/${room.id}/game/private-messages`)
      .then((items) => { if (active) { setMessages(items); setError('') } })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : 'Không tải được tin riêng.') }) }
    load()
    const timer = window.setInterval(load, 8000)
    return () => { active = false; window.clearInterval(timer) }
  }, [room.id])

  return <section className="private-inbox" aria-labelledby="private-inbox-title">
    <span className="eyebrow">CHỈ BẠN VÀ QUẢN TRÒ</span><h2 id="private-inbox-title">Tin riêng từ quản trò</h2>
    <p>Tin trong ván chỉ bạn và quản trò xem được; sau trận, toàn bộ trao đổi sẽ nằm trong nhật ký của phòng.</p>
    {error && <p className="form-message" role="alert">{error}</p>}
    <MessageList messages={messages} />
  </section>
}

export function HostPrivateMessages({ room, playerId }: { room: Room; playerId: string }) {
  const [messages, setMessages] = useState<PrivateMessage[]>([])
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const requestIdFor = useRequestIds()
  const path = `/api/rooms/${room.id}/game/players/${playerId}/private-messages`

  async function load() { setMessages(await api<PrivateMessage[]>(path)) }
  useEffect(() => { void load().catch((cause) => setError(cause instanceof Error ? cause.message : 'Không tải được tin riêng.')) }, [path])

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !text.trim()) return
    setBusy(true)
    setError('')
    const payload = { text: text.trim() }
    const request = requestIdFor({ room_id: room.id, player_id: playerId, payload })
    void api(path, { method: 'POST', body: jsonBody({ client_request_id: request.id,
      ...payload }) }).then(() => {
      request.acknowledge()
      setText('')
      void load().catch(() => setError('Đã gửi tin, nhưng chưa tải lại được. Mở lại tab để xem.'))
    }).catch((cause) => setError(cause instanceof Error ? cause.message : 'Không gửi được tin riêng.'))
      .finally(() => setBusy(false))
  }

  async function changeMessage(message: PrivateMessage, action: 'edit' | 'delete') {
    if (busy) return
    const nextText = editText.trim()
    if (action === 'edit' && (!nextText || nextText === message.text)) return
    if (action === 'delete' && !window.confirm('Xóa tin riêng này khỏi cuộc trò chuyện? Bản gốc vẫn được giữ trong sự kiện để đưa vào hồ sơ sau trận.')) return
    const payload = { action, text: action === 'edit' ? nextText : null }
    const request = requestIdFor({ room_id: room.id, source_event_id: message.event_id, payload })
    setBusy(true)
    setError('')
    let sent = false
    try {
      await api(`/api/rooms/${room.id}/game/content/${message.event_id}/revisions`, {
        method: 'POST', body: jsonBody({ client_request_id: request.id, ...payload }),
      })
      sent = true
      request.acknowledge()
      setEditingId(null)
      setEditText('')
      await load()
    } catch (cause) {
      setError(sent ? 'Đã cập nhật tin nhưng chưa tải lại được. Mở lại tab để kiểm tra.'
        : cause instanceof Error ? cause.message : 'Không cập nhật được tin riêng.')
    } finally { setBusy(false) }
  }

  return <div className="host-private-messages">
    <p>Chỉ người nhận và quản trò thấy trong trận. Nội dung gốc và các lần sửa/xóa được giữ để công bố trong hồ sơ sau trận.</p>
    {room.status === 'playing' && <form className="stack-form" onSubmit={send}>
      <label htmlFor="private-message-text">Nội dung gửi cho người chơi</label>
      <textarea id="private-message-text" value={text} maxLength={2000}
        onChange={(event) => setText(event.target.value)} />
      <button disabled={busy || !text.trim()}>Gửi tin riêng</button>
    </form>}
    {error && <p className="form-message" role="alert">{error}</p>}
    {messages.length ? <ol className="private-message-list">{messages.map((message) =>
      <li key={message.id}>
        <small><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleString('vi-VN')}</time>
          {message.edited_at && ' · Đã sửa'}</small>
        {editingId === message.event_id ? <form className="stack-form inline-edit-form"
          onSubmit={(event) => { event.preventDefault(); void changeMessage(message, 'edit') }}>
          <label htmlFor={`edit-message-${message.id}`}>Sửa tin riêng</label>
          <textarea id={`edit-message-${message.id}`} maxLength={2000} value={editText}
            onChange={(event) => setEditText(event.target.value)} />
          <div className="inline-actions"><button disabled={busy || !editText.trim() || editText.trim() === message.text}>Lưu</button>
            <button type="button" className="button-secondary" onClick={() => setEditingId(null)}>Hủy</button></div>
        </form> : <p>{message.text}</p>}
        {room.status === 'playing' && editingId !== message.event_id && <div className="inline-actions">
          <button type="button" className="button-secondary" disabled={busy}
            onClick={() => { setEditingId(message.event_id); setEditText(message.text) }}>Sửa</button>
          <button type="button" className="button-secondary" disabled={busy}
            onClick={() => void changeMessage(message, 'delete')}>Xóa</button>
        </div>}
      </li>)}</ol> : <p>Chưa có tin riêng từ quản trò.</p>}
  </div>
}
