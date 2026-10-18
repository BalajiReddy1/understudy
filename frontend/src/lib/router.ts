import { useEffect, useState } from 'react'

// A few pages don't need a router library: the path and query string live in the URL,
// navigate() pushes history, and back/forward re-render.

export function navigate(to: string, replace = false): void {
  const from = location.pathname
  if (replace) history.replaceState(null, '', to)
  else history.pushState(null, '', to)
  window.dispatchEvent(new PopStateEvent('popstate'))
  // A new page starts at the top, as it would after a full load. Changing only the
  // query (picking a system) keeps the reader where they are.
  if (!replace && location.pathname !== from) window.scrollTo(0, 0)
}

export function useLocation(): { path: string; params: URLSearchParams } {
  const read = () => ({ path: location.pathname, params: new URLSearchParams(location.search) })
  const [loc, setLoc] = useState(read)
  useEffect(() => {
    const onPop = () => setLoc(read())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  return loc
}

/** Update query parameters without a new history entry (selection, not navigation). */
export function setParams(updates: Record<string, string | null>): void {
  const params = new URLSearchParams(location.search)
  for (const [k, v] of Object.entries(updates)) {
    if (v === null) params.delete(k)
    else params.set(k, v)
  }
  const q = params.toString()
  navigate(`${location.pathname}${q ? `?${q}` : ''}`, true)
}
