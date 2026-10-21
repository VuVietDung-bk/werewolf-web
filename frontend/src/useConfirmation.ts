import { createElement, useCallback, useEffect, useRef, useState } from 'react'
import ConfirmationDialog, { type ConfirmationDetails } from './components/ConfirmationDialog'

export function useConfirmation() {
  const [details, setDetails] = useState<ConfirmationDetails | null>(null)
  const resolver = useRef<((confirmed: boolean) => void) | null>(null)

  const settle = useCallback((confirmed: boolean) => {
    const resolve = resolver.current
    if (!resolve) return
    resolver.current = null
    setDetails(null)
    resolve(confirmed)
  }, [])

  useEffect(() => () => {
    resolver.current?.(false)
    resolver.current = null
  }, [])

  const ask = useCallback((next: ConfirmationDetails): Promise<boolean> => {
    if (resolver.current) return Promise.resolve(false)
    return new Promise((resolve) => {
      resolver.current = resolve
      setDetails(next)
    })
  }, [])

  return { ask, confirmationDialog: createElement(ConfirmationDialog, { details, settle }) }
}
