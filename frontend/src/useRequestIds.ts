// Pending ids survive tab/component changes within this page session.
const pending = new Map<string, string>()

/** Keep one id for retries of the same command until the API confirms it. */
export function useRequestIds() {
  return (command: unknown) => {
    const key = JSON.stringify(command)
    let id = pending.get(key)
    if (!id) {
      id = crypto.randomUUID()
      pending.set(key, id)
    }
    return { id, acknowledge: () => { pending.delete(key) } }
  }
}
