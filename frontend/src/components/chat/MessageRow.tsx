import { useEffect, useId, useRef, useState } from 'react'
import type { DisplayMessage } from './presentation'
import EmojiPicker from './EmojiPicker'

export default function MessageRow({ item, grouped, canEdit, canReact, busy, onReaction, onEdit, onDelete, editing, editText, onEditText, onSaveEdit, onCancelEdit }: {
  item: DisplayMessage; grouped: boolean; canEdit: boolean; busy: boolean
  onEdit: () => void; onDelete: () => void; editing: boolean; editText: string
  onEditText: (value: string) => void; onSaveEdit: () => void; onCancelEdit: () => void
  canReact:boolean;onReaction:(emoji:string,present:boolean)=>void
}) {
  const [menu, setMenu] = useState(false)
  const [picker,setPicker]=useState(false)
  const reactionTrigger=useRef<HTMLButtonElement>(null)
  const editorId = useId()
  const menuRef = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!menu) return
    menuRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const dismiss = (event: PointerEvent) => { if (!menuRef.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) setMenu(false) }
    document.addEventListener('pointerdown',dismiss)
    return () => document.removeEventListener('pointerdown',dismiss)
  }, [menu])
  const time = new Date(item.createdAt).toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'})
  return <li className={`u3-message ${grouped ? 'u3-grouped' : 'u3-group-start'} ${item.kind === 'private' ? 'u3-private' : ''} ${canEdit&&canReact?'u3-double-tools':''}`} data-item-key={item.key}>
    {grouped ? <time className="u3-compact-time" dateTime={item.createdAt} title={new Date(item.createdAt).toLocaleString('vi-VN')}>{time}</time>
      : <span className="u3-avatar" aria-hidden="true">{item.isHost ? '☾' : item.authorName.slice(0,1).toUpperCase()}</span>}
    <div className="u3-message-content">
      {!grouped && <div className="u3-message-heading"><strong>{item.authorName}</strong>{item.isHost && <span className="chat-host-badge">Quản trò</span>}
        {item.kind === 'private' && <small>Tin gửi riêng</small>}<time dateTime={item.createdAt} title={new Date(item.createdAt).toLocaleString('vi-VN')}>{time}</time></div>}
      <p>{item.deleted ? 'Tin nhắn đã xóa' : item.text}{item.editedAt && !item.deleted && <small> · đã sửa</small>}</p>
      {Boolean(item.original?.reactions?.length)&&<div className="u3-reactions" role="group" aria-label="Reaction của tin nhắn">
        {item.original!.reactions.map(reaction=><button key={reaction.emoji} type="button" className={`u3-reaction ${reaction.mine?'mine':''}`}
          aria-pressed={reaction.mine} aria-label={`Reaction ${reaction.emoji}: ${reaction.count}${reaction.mine?', bạn đã chọn':''}`}
          disabled={busy||!canReact} onClick={()=>onReaction(reaction.emoji,!reaction.mine)}>
          <span>{reaction.emoji}</span><span>{reaction.count}</span>
        </button>)}
      </div>}
      {item.original?.history && <details className="u3-history"><summary>Lịch sử đầy đủ ({item.original.history.length} phiên bản)</summary>
        <ol>{item.original.history.map((entry,index)=><li key={`${index}:${entry.action}:${entry.created_at}`}><time dateTime={entry.created_at}>{new Date(entry.created_at).toLocaleString('vi-VN')}</time>
          <strong>{entry.action==='send'?' Gửi':entry.action==='edit'?' Sửa':' Xóa'}</strong>{entry.text&&<p>{entry.text}</p>}</li>)}</ol>
        <p><a href="#management/history">Lịch sử thêm/gỡ reaction trong nhật ký sau trận</a></p>
      </details>}
      {editing && <form className="u3-edit" onSubmit={(event) => { event.preventDefault(); onSaveEdit() }}>
        <label htmlFor={editorId}>Chỉnh tin nhắn</label>
        <textarea id={editorId} autoFocus value={editText} maxLength={2000} onChange={(event) => onEditText(event.target.value)} />
        <div><button type="submit" disabled={busy || !editText.trim() || editText.trim() === item.text}>Lưu</button><button type="button" onClick={onCancelEdit}>Hủy</button></div>
      </form>}
    </div>
    {(canEdit||canReact) && <div className="u3-message-tools">
      {canReact&&<button ref={reactionTrigger} type="button" className="u3-menu-trigger u3-react-trigger" disabled={busy}
        aria-label={`Thêm reaction cho tin của ${item.authorName}`} title="Thêm reaction" onClick={()=>setPicker(true)}>☺+</button>}
      {canEdit&&<button ref={trigger} type="button" className="u3-menu-trigger" aria-label={`Thao tác tin của ${item.authorName}`} aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>⋯</button>}
      {menu && <div className="u3-message-menu" role="menu" ref={menuRef} onKeyDown={(event) => {
        const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button'))
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
        if (event.key === 'Escape') { event.preventDefault(); setMenu(false); trigger.current?.focus() }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); buttons[(index+(event.key === 'ArrowDown' ? 1 : -1)+buttons.length)%buttons.length]?.focus() }
      }}><button type="button" role="menuitem" onClick={() => { setMenu(false); onEdit() }}>Sửa</button><button type="button" role="menuitem" onClick={() => { setMenu(false); onDelete() }}>Xóa</button></div>}
    </div>}
    {picker&&<EmojiPicker onPick={emoji=>{setPicker(false);onReaction(emoji,true)}}
      onClose={()=>{setPicker(false);reactionTrigger.current?.focus()}}/>}
  </li>
}
