import { useEffect, useState } from 'react'

type PixelAssetProps = {
  src: string
  alt: string
  baseWidth: number
  baseHeight: number
  scale: number
  className?: string
}

/** Render at integer scale and preserve a label when the image fails. */
export default function PixelAsset({
  src, alt, baseWidth, baseHeight, scale, className = '',
}: PixelAssetProps) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [src])
  const safeScale = Math.max(1, Math.floor(scale))
  const size = { width: baseWidth * safeScale, height: baseHeight * safeScale }

  if (failed) {
    return <span className={`pixel-fallback ${className}`} style={size} role="img" aria-label={alt}>
      <span aria-hidden="true">✦</span><span>{alt}</span>
    </span>
  }
  return <img className={`pixel-asset ${className}`} src={src} alt={alt}
    width={size.width} height={size.height} onError={() => setFailed(true)} />
}
