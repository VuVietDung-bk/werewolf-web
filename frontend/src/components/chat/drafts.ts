const PREFIX = 'ma-soi-draft-v1:'
const memory = new Map<string, string>()
export const draftKey = (user: string, room: string, scope: string) => `${PREFIX}${user}:${room}:${scope}`
export function readDraft(key: string): string {
  try { return sessionStorage.getItem(key) ?? memory.get(key) ?? '' } catch { return memory.get(key) ?? '' }
}
export function writeDraft(key: string, text: string) {
  memory.set(key, text)
  try { if (text) sessionStorage.setItem(key, text); else sessionStorage.removeItem(key) } catch { /* retain in this page session */ }
}
export function clearChatDrafts() {
  memory.clear()
  try {
    for (const key of Object.keys(sessionStorage)) if (key.startsWith(PREFIX)) sessionStorage.removeItem(key)
  } catch { /* storage can be unavailable */ }
}
