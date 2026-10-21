import type { ChatChannel } from '../api'

/** Three small pixel grid symbols, paired with visible channel names. */
export default function ChatGlyph({ channel }: { channel: ChatChannel }) {
  return <svg className={`chat-glyph chat-glyph-${channel}`} width="28" height="28"
    viewBox="0 0 24 24" shapeRendering="crispEdges" aria-hidden="true" focusable="false">
    {channel === 'common' && <>
      <rect x="2" y="3" width="20" height="14" fill="#74b7c9" />
      <rect x="5" y="6" width="14" height="8" fill="#101626" />
      <rect x="6" y="17" width="4" height="4" fill="#74b7c9" />
      <rect x="10" y="9" width="4" height="2" fill="#f4e8d0" />
      <rect x="16" y="9" width="2" height="2" fill="#f4e8d0" />
    </>}
    {channel === 'team' && <>
      <rect x="3" y="4" width="8" height="10" fill="#a6d8ae" />
      <rect x="5" y="6" width="4" height="5" fill="#101626" />
      <rect x="13" y="4" width="8" height="10" fill="#a6d8ae" />
      <rect x="15" y="6" width="4" height="5" fill="#101626" />
      <rect x="5" y="14" width="14" height="3" fill="#a6d8ae" />
      <rect x="9" y="17" width="6" height="3" fill="#a6d8ae" />
    </>}
    {channel === 'host' && <>
      <rect x="3" y="5" width="18" height="14" fill="#e9b66f" />
      <rect x="5" y="7" width="14" height="9" fill="#101626" />
      <rect x="7" y="9" width="10" height="2" fill="#e9b66f" />
      <rect x="9" y="11" width="6" height="2" fill="#e9b66f" />
      <rect x="11" y="13" width="2" height="2" fill="#e9b66f" />
      <rect x="10" y="17" width="4" height="4" fill="#e9b66f" />
    </>}
  </svg>
}
