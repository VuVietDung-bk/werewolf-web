type StatusGlyphKind = 'effect' | 'dead' | 'revived'

/** Small grid-drawn symbols; the adjacent text always carries the meaning. */
export default function StatusGlyph({ kind, size = 22 }: { kind: StatusGlyphKind; size?: number }) {
  return <svg className={`status-glyph status-glyph-${kind}`} width={size} height={size}
    viewBox="0 0 24 24" shapeRendering="crispEdges" aria-hidden="true" focusable="false">
    {kind === 'effect' && <>
      <rect x="10" y="1" width="4" height="6" fill="#f2cb80" />
      <rect x="10" y="17" width="4" height="6" fill="#f2cb80" />
      <rect x="1" y="10" width="6" height="4" fill="#f2cb80" />
      <rect x="17" y="10" width="6" height="4" fill="#f2cb80" />
      <rect x="8" y="8" width="8" height="8" fill="#d6904e" />
      <rect x="10" y="10" width="4" height="4" fill="#fff1ba" />
    </>}
    {kind === 'dead' && <>
      <rect x="6" y="5" width="12" height="17" fill="#9a4d61" />
      <rect x="8" y="3" width="8" height="2" fill="#d88a85" />
      <rect x="5" y="8" width="2" height="14" fill="#d88a85" />
      <rect x="17" y="8" width="2" height="14" fill="#5a344e" />
      <rect x="10" y="8" width="4" height="9" fill="#f3c8b2" />
      <rect x="8" y="11" width="8" height="3" fill="#f3c8b2" />
      <rect x="3" y="21" width="18" height="2" fill="#d88a85" />
    </>}
    {kind === 'revived' && <>
      <rect x="10" y="2" width="4" height="20" fill="#84d2a2" />
      <rect x="2" y="10" width="20" height="4" fill="#84d2a2" />
      <rect x="8" y="8" width="8" height="8" fill="#b9f2b8" />
      <rect x="10" y="10" width="4" height="4" fill="#f5f0b0" />
      <rect x="3" y="3" width="2" height="2" fill="#f5f0b0" />
      <rect x="19" y="19" width="2" height="2" fill="#f5f0b0" />
    </>}
  </svg>
}
