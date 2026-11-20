import { useEffect, useRef, useState } from 'react'
import { findEmoji, recentEmoji, rememberEmoji, type Emoji } from './emoji'

export default function EmojiPicker({ onPick, onClose }: { onPick: (value: string) => void; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState('')
  const results = findEmoji(query)
  const recent = query ? [] : recentEmoji()
  useEffect(() => { dialog.current?.showModal(); search.current?.focus() }, [])
  const close = () => { dialog.current?.close(); onClose() }
  const pick = (emoji: Emoji) => { rememberEmoji(emoji.value); dialog.current?.close(); onPick(emoji.value) }
  return <dialog ref={dialog} className="u3-emoji-dialog" aria-labelledby="emoji-picker-title"
    onCancel={(event) => { event.preventDefault(); close() }} onClose={onClose}
    onKeyDown={(event) => {
      if (event.key !== 'Tab') return
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button,input'))
      if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1)?.focus() }
      if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0]?.focus() }
    }}>
    <header><h2 id="emoji-picker-title">Chọn emoji</h2><button type="button" onClick={close} aria-label="Đóng bộ chọn emoji">✕</button></header>
    <input ref={search} value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Tìm emoji" placeholder="Tìm tên hoặc :alias:, ví dụ sói, :wolf:"
      onKeyDown={(event) => { if (event.key === 'ArrowDown') { event.preventDefault(); dialog.current?.querySelector<HTMLButtonElement>('[data-emoji]')?.focus() } }} />
    <div className="u3-emoji-results" onKeyDown={(event) => {
      const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[data-emoji]'))
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
      if (index < 0) return
      const move: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 6, ArrowUp: -6 }
      if (event.key in move) { event.preventDefault(); buttons[Math.max(0, Math.min(buttons.length-1,index+move[event.key]))]?.focus() }
      if (event.key === 'Home') { event.preventDefault(); buttons[0]?.focus() }
      if (event.key === 'End') { event.preventDefault(); buttons.at(-1)?.focus() }
    }}>
      {recent.length > 0 && <><h3>Gần dùng</h3><div className="u3-emoji-grid">{recent.map((emoji) => <button key={emoji.value} type="button" data-emoji={emoji.value} title={emoji.name} aria-label={emoji.name} onClick={() => pick(emoji)}>{emoji.value}</button>)}</div></>}
      <h3>{query ? 'Kết quả' : 'Emoji'}</h3><div className="u3-emoji-grid">{results.map((emoji) => <button key={emoji.value} type="button" data-emoji={emoji.value} title={`${emoji.name} :${emoji.aliases[0]}:`} aria-label={emoji.name} onClick={() => pick(emoji)}>{emoji.value}</button>)}</div>
      {!results.length && <p role="status">Không tìm thấy emoji. Bạn vẫn có thể dán emoji trực tiếp vào tin nhắn.</p>}
    </div>
  </dialog>
}
