// Per-visitor conveniences kept in the browser: the session copy they're working on and
// an id for their agent memory. Storage can be blocked (private windows, strict
// settings), so every read and write falls back quietly.

export function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

export function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* storage unavailable: the value lasts for this page only */
  }
}

/** A random id for this visitor's agent memory, made once and kept. */
export function visitorId(): string {
  let id = read('understudy.visitor')
  if (!id || !/^[a-zA-Z0-9_-]{1,40}$/.test(id)) {
    id = `v-${crypto.randomUUID().replace(/-/g, '').slice(0, 20)}`
    write('understudy.visitor', id)
  }
  return id
}
