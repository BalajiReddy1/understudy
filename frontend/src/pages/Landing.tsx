import { type FormEvent, useEffect, useState } from 'react'
import { type BoardRow, type Rehearsal, api } from '../api'
import { Footer } from '../components/Footer'
import { Header } from '../components/Header'
import { StagePanel } from '../components/StagePanel'
import { splitPath } from '../lib/format'
import { navigate } from '../lib/router'

const QUESTIONS = [
  'Which Kubernetes systems are most at risk?',
  'How exposed is the kubelet config API, and what is the cheapest fix?',
  'Why is pkg/kubelet/cm built the way it is?',
]

export function Landing() {
  const [board, setBoard] = useState<BoardRow[] | null>(null)
  const [top, setTop] = useState<BoardRow | null>(null)
  const [selected, setSelected] = useState('billing')
  const [rehearsal, setRehearsal] = useState<Rehearsal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [question, setQuestion] = useState('')

  useEffect(() => {
    api
      .board('sample')
      .then(setBoard)
      .catch((e: Error) => setError(e.message))
    // The headline band uses the real top of the Kubernetes board, not a typed-in claim.
    api
      .board('k8s')
      .then((rows) => setTop(rows[0] ?? null))
      .catch(() => setTop(null))
  }, [])

  const after = new Map((rehearsal?.changes ?? []).map((c) => [c.system, c.after]))
  const ask = (q: string) => navigate(`/k8s?ask=${encodeURIComponent(q)}`)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (question.trim()) ask(question.trim())
  }
  const go = (to: string) => (e: React.MouseEvent) => {
    e.preventDefault()
    navigate(to)
  }

  return (
    <div className="page">
      <Header />
      <section className="hero">
        <div className="wrap hero-grid">
          <div className="hero-text">
            <h1>Understudy</h1>
            <p className="hero-sub">A company brain that knows who can step in.</p>
            <p className="hero-question">
              How many people could you lose before nobody knows how a system works?
            </p>
            <p className="hero-body">
              Counting the people who know a system makes it look safe. Understudy counts the separate
              routes from them to the people who approve its changes, with max-flow on a FalkorDB graph
              of Kubernetes, and an agent rehearses departures on a copy of the graph before they happen.
            </p>
            <div className="hero-actions">
              <a className="button button-primary button-large" href="/k8s" onClick={go('/k8s')}>
                Open the Kubernetes call board
              </a>
              <a className="button button-large button-ghost" href="/how" onClick={go('/how')}>
                How it works
              </a>
            </div>
            <dl className="hero-meta">
              <div>
                <dt>Tracks</dt>
                <dd>Company Brain, and Agents That Act on Connected Data</dd>
              </div>
              <div>
                <dt>Built on</dt>
                <dd>
                  FalkorDB: <code>algo.maxFlow</code>, <code>GRAPH.COPY</code>
                </dd>
              </div>
              <div>
                <dt>Data</dt>
                <dd>9,187 merged pull requests and every OWNERS file in Kubernetes since January 2024</dd>
              </div>
            </dl>
          </div>

          <div className="hero-demo">
            <p className="demo-label">Sample org, seven people. Take someone off stage.</p>
            {error && (
              <p className="panel-error" role="alert">
                {error}
              </p>
            )}
            {board && (
              <div className="demo-systems" role="group" aria-label="Sample systems">
                {board.map((r) => {
                  const n = after.get(r.name) ?? r.understudy
                  return (
                    <button
                      key={r.name}
                      type="button"
                      className={`demo-system${r.name === selected ? ' is-selected' : ''}`}
                      aria-pressed={r.name === selected}
                      onClick={() => setSelected(r.name)}
                    >
                      <span className={`badge${n <= 1 ? ' is-alarm' : ''}`}>{n}</span>
                      {r.name}
                    </button>
                  )
                })}
              </div>
            )}
            <StagePanel
              org="sample"
              system={selected}
              session={null}
              version={0}
              highlight={new Set()}
              rehearsal={rehearsal}
              onRehearse={async (people) => setRehearsal(await api.rehearse('sample', people))}
              onClearRehearsal={() => setRehearsal(null)}
              onSelectSystem={setSelected}
            />
          </div>
        </div>
      </section>

      {top && (
        <section className="headline" aria-labelledby="headline-title">
          <div className="wrap headline-grid">
            <div>
              <p className="headline-path" id="headline-title">
                {splitPath(top.name).lead}
                <strong>{splitPath(top.name).tail}</strong>, the top of the Kubernetes call board
              </p>
              <p className="headline-line">
                <span className="headline-n">{top.knowers}</span> people know it.{' '}
                <span className="headline-n">{top.approvers}</span> can approve its changes.{' '}
                <span className={`headline-n${top.understudy <= 1 ? ' is-alarm' : ''}`}>{top.understudy}</span>{' '}
                {top.understudy === 1 ? 'route' : 'routes'} between them.
              </p>
            </div>
            <div className="headline-aside">
              <p>
                A headcount says this part of Kubernetes is well covered.{' '}
                {top.understudy === 1
                  ? 'The graph says every route from someone who knows the code to someone who can approve a change runs through one person.'
                  : `The graph finds only ${top.understudy} separate routes from someone who knows the code to someone who can approve a change.`}{' '}
                That count of separate routes is its understudy number.
              </p>
              <a
                className="button button-primary"
                href={`/k8s?system=${encodeURIComponent(top.name)}`}
                onClick={go(`/k8s?system=${encodeURIComponent(top.name)}`)}
              >
                See the routes on its stage
              </a>
            </div>
          </div>
        </section>
      )}

      <section className="ask-k8s" aria-labelledby="ask-title">
        <div className="wrap ask-grid">
          <div>
            <h2 id="ask-title">Ask about Kubernetes</h2>
            <p>
              79 systems, 364 people and the routes between them, in one FalkorDB graph. The agent picks
              from fixed graph tools, never writes Cypher, and shows each query it ran under its answer.
            </p>
          </div>
          <div>
            <form className="ask-form" onSubmit={submit}>
              <label className="visually-hidden" htmlFor="landing-ask">
                Ask about Kubernetes
              </label>
              <input
                id="landing-ask"
                type="text"
                maxLength={500}
                placeholder="What happens if someone steps back?"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
              />
              <button type="submit" className="button button-primary" disabled={!question.trim()}>
                Ask
              </button>
            </form>
            <ul className="ask-suggestions">
              {QUESTIONS.map((q) => (
                <li key={q}>
                  <button type="button" className="suggestion" onClick={() => ask(q)}>
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>
      <Footer />
    </div>
  )
}
