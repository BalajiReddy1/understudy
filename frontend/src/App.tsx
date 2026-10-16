import { Header } from './components/Header'
import { How } from './pages/How'
import { Landing } from './pages/Landing'
import { Proof } from './pages/Proof'
import { Workspace } from './pages/Workspace'
import { navigate, useLocation } from './lib/router'

export function App() {
  const { path, params } = useLocation()
  if (path === '/') return <Landing />
  if (path === '/k8s') return <Workspace params={params} />
  if (path === '/proof') return <Proof />
  if (path === '/how') return <How />
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
