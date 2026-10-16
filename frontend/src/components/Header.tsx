import { navigate, useLocation } from '../lib/router'

const LINKS = [
  ['/', 'Sample org'],
  ['/k8s', 'Kubernetes'],
  ['/proof', 'Proof'],
]

interface Props {
  /** The landing page has the name as its title, so the bar shows only the links. */
  wordmark?: boolean
  session?: string | null
  onReset?: () => void
  onLeaveCopy?: () => void
}

export function Header({ wordmark = true, session, onReset, onLeaveCopy }: Props) {
  const { path } = useLocation()
  const go = (to: string) => (e: React.MouseEvent) => {
    e.preventDefault()
    navigate(to)
  }
  return (
    <header className="site-head">
      {wordmark && (
        <a className="wordmark" href="/" onClick={go('/')}>
          Understudy
        </a>
      )}
      <nav aria-label="Main">
        {LINKS.map(([to, label]) => (
          <a key={to} href={to} onClick={go(to)} aria-current={path === to ? 'page' : undefined}>
            {label}
          </a>
        ))}
        <a href="https://github.com/BalajiReddy1/understudy" target="_blank" rel="noreferrer">
          Code
        </a>
      </nav>
      {session && (
        <div className="copy-status" role="status">
          <span>You’re working on your own copy</span>
          <button type="button" className="button button-small" onClick={onReset}>
            Reset it
          </button>
          <button type="button" className="button button-small button-quiet" onClick={onLeaveCopy}>
            Back to the real graph
          </button>
        </div>
      )}
    </header>
  )
}
