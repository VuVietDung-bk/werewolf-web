import { useLayoutEffect, useRef, useState } from 'react'
import EmojiPicker from './EmojiPicker'

export default function MessageComposer({ value, onChange, onSend, busy, slowRemaining, channelName, scope }: {
  value: string; onChange: (text: string) => void; onSend: () => void; busy: boolean; slowRemaining: number; channelName: string; scope: string
}) {
  const input = useRef<HTMLTextAreaElement>(null)
  const composing = useRef(false)
  const selection = useRef({ start: 0, end: 0 })
  const [emojiOpen, setEmojiOpen] = useState(false)
  const [emojiError, setEmojiError] = useState('')
  useLayoutEffect(() => {
    const element = input.current
    if (element) {
      element.style.height = '0px'
      element.style.height = `${Math.min(120,Math.max(40,element.scrollHeight))}px`
    }
  }, [value, scope])
  useLayoutEffect(() => { setEmojiOpen(false); setEmojiError('') }, [scope])
  const blocked = busy || slowRemaining > 0 || !value.trim()
  const send = () => { if (!blocked && !composing.current) onSend() }
  return <div className="u3-composer-wrap">
    <form className="chat-send u3-composer" onSubmit={(event) => { event.preventDefault(); send() }}>
      <button type="button" className="u3-emoji-trigger" aria-label="Chọn emoji" aria-haspopup="dialog"
        onClick={() => { selection.current = { start: input.current?.selectionStart ?? value.length, end: input.current?.selectionEnd ?? value.length }; setEmojiOpen(true) }}>☺</button>
      <textarea ref={input} id="chat-text" aria-label="Nội dung tin nhắn" value={value} maxLength={2000} rows={1}
        placeholder={`Nhắn vào ${channelName}…`} onChange={(event) => onChange(event.target.value)}
        onCompositionStart={() => { composing.current = true }} onCompositionEnd={() => { composing.current = false }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229 && !composing.current) {
            event.preventDefault(); send()
          }
        }} />
      <button type="submit" className="u3-send" disabled={blocked} aria-label="Gửi tin">{busy ? '…' : '➤'}</button>
    </form>
    <div className="u3-composer-help"><span>{slowRemaining > 0 ? `Slow mode: gửi tiếp sau ${slowRemaining}s` : 'Enter gửi · Shift + Enter xuống dòng'}</span><span>{value.length}/2000</span></div>
    {emojiError && <small role="status">{emojiError}</small>}
    {emojiOpen && <EmojiPicker onClose={() => { setEmojiOpen(false); input.current?.focus() }} onPick={(emoji) => {
      const { start, end } = selection.current
      if (value.length-(end-start)+emoji.length > 2000) { setEmojiError('Tin nhắn đã đạt giới hạn 2.000 ký tự.'); setEmojiOpen(false); input.current?.focus(); return }
      setEmojiError('')
      onChange(value.slice(0,start)+emoji+value.slice(end));setEmojiOpen(false)
      requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(start+emoji.length,start+emoji.length) })
    }} />}
  </div>
}
