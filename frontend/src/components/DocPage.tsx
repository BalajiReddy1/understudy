import { type ReactNode, useEffect, useState } from 'react'
import { Footer } from './Footer'
import { Header } from './Header'

export interface DocSection {
  id: string
  title: string
}

interface Props {
  title: string
  /** The page's answer, under the title on the green. */
  lead: ReactNode
  /** What sits beside the title: a real artifact, not decoration. */
  aside?: ReactNode
  sections: DocSection[]
  children: ReactNode
}

/** The section nearest the top of the window, for the contents rail. `key` is the
 * section ids joined by "|", so the observer is set up once, not on every render. */
function useCurrent(key: string): string | null {
  const [current, setCurrent] = useState<string | null>(null)
  useEffect(() => {
    const ids = key.split('|')
    const els = ids.map((id) => document.getElementById(id)).filter((el): el is HTMLElement => el !== null)
    if (!els.length) return
    const seen = new Map<string, boolean>()
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) seen.set(e.target.id, e.isIntersecting)
        const first = ids.find((id) => seen.get(id))
        if (first) setCurrent(first)
      },
      { rootMargin: '-15% 0px -60% 0px' },
    )
    els.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [key])
  return current
}

export function DocPage({ title, lead, aside, sections, children }: Props) {
  const current = useCurrent(sections.map((s) => s.id).join('|'))
  return (
    <div className="page doc-page">
      <Header />
      <header className="doc-hero">
        <div className={`wrap doc-hero-grid${aside ? '' : ' is-single'}`}>
          <div className="doc-hero-text">
            <h1>{title}</h1>
            <div className="doc-lead">{lead}</div>
          </div>
          {aside && <div className="doc-hero-aside">{aside}</div>}
        </div>
      </header>
      <div className="wrap doc-body">
        <nav className="doc-rail" aria-label="On this page">
          <p className="doc-rail-head">On this page</p>
          <ol>
            {sections.map((s) => (
              <li key={s.id}>
                <a href={`#${s.id}`} aria-current={current === s.id ? 'location' : undefined}>
                  {s.title}
                </a>
              </li>
            ))}
          </ol>
        </nav>
        <main className="doc-main">{children}</main>
      </div>
      <Footer />
    </div>
  )
}
