import { Header } from './components/Header'
import { Landing } from './pages/Landing'
import { Workspace } from './pages/Workspace'
import { navigate, useLocation } from './lib/router'

export function App() {
  const { path, params } = useLocation()
  if (path === '/') return <Landing />
  if (path === '/k8s') return <Workspace params={params} />
  return (
    <div className="landing">
      <Header />
      <main className="not-found">
        <h1>Nothing on stage here</h1>
        <p>That page doesn’t exist.</p>
        <button type="button" className="button button-primary" onClick={() => navigate('/')}>
          Go to the start
        </button>
      </main>
    </div>
  )
}
