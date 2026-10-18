import { navigate } from '../lib/router'

const REPO = 'https://github.com/BalajiReddy1/understudy'

export function Footer() {
  const go = (to: string) => (e: React.MouseEvent) => {
    e.preventDefault()
    navigate(to)
  }
  return (
    <footer className="site-foot">
      <div className="wrap foot-grid">
        <div className="foot-brand">
          <p className="foot-mark">Understudy</p>
          <p>
            How many people a team can lose before nobody who approves its changes can reach anyone
            who knows the code. Measured with max-flow on a FalkorDB graph of Kubernetes.
          </p>
        </div>
        <nav className="foot-links" aria-label="Footer">
          <p className="foot-head">On this site</p>
          <a href="/" onClick={go('/')}>
            The sample org
          </a>
          <a href="/k8s" onClick={go('/k8s')}>
            Kubernetes call board
          </a>
          <a href="/proof" onClick={go('/proof')}>
            Does the number predict anything?
          </a>
          <a href="/how" onClick={go('/how')}>
            How it works
          </a>
        </nav>
        <div className="foot-links">
          <p className="foot-head">Elsewhere</p>
          <a href={REPO} target="_blank" rel="noreferrer">
            Source code
          </a>
          <a href={`${REPO}#readme`} target="_blank" rel="noreferrer">
            README and setup
          </a>
          <a href="/api/proof">Backtest data (JSON)</a>
        </div>
        <p className="foot-note">
          Built solo for Graph Hacks 2026, WeMakeDevs × FalkorDB. People appear as pseudonyms; the
          numbers come from public Kubernetes history and are a model, not a verdict on anyone.
        </p>
      </div>
    </footer>
  )
}
