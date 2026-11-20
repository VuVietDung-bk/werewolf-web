export const PENDING_CHAT_PREFIX = 'ma-soi-chat-pending:v1:'

/** Retain the command ID across reloads if the server stored a message but its ACK was lost. */
export function pendingChatRequest(scope: string, payload: unknown, fallbackId: string, compatiblePayloads: unknown[] = []) {
  const key = PENDING_CHAT_PREFIX + scope
  const command = JSON.stringify(payload)
  const compatible = new Set([command, ...compatiblePayloads.map((item) => JSON.stringify(item))])
  let id = fallbackId
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (saved && typeof saved === 'object' && 'command' in saved && typeof saved.command === 'string' && compatible.has(saved.command) &&
        'id' in saved && typeof saved.id === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(saved.id)) id = saved.id
    sessionStorage.setItem(key, JSON.stringify({ command, id }))
  } catch { /* use the page-session ID when storage is unavailable */ }
  return { id, acknowledge: () => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || 'null')
      if (saved?.id === id) sessionStorage.removeItem(key)
    } catch { /* optional persistence */ }
  } }
}
