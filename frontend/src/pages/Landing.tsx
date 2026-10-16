import { type FormEvent, useEffect, useState } from 'react'
import { type BoardRow, type Rehearsal, api } from '../api'
import { Header } from '../components/Header'
import { StagePanel } from '../components/StagePanel'
import { navigate } from '../lib/router'

const QUESTIONS = [
  'Which Kubernetes systems are most at risk?',
  'How exposed is the kubelet config API, and what is the cheapest fix?',
  'Why is pkg/kubelet/cm built the way it is?',
]

export function Landing() {
  const [board, setBoard] = useState<BoardRow[] | null>(null)
  const [selected, setSelected] = useState('billing')
  const [rehearsal, setRehearsal] = useState<Rehearsal | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [question, setQuestion] = useState('')

  useEffect(() => {
    api
      .board('sample')
      .then(setBoard)
      .catch((e: Error) => setError(e.message))
  }, [])

  const after = new Map((rehearsal?.changes ?? []).map((c) => [c.system, c.after]))
  const ask = (q: string) => navigate(`/k8s?ask=${encodeURIComponent(q)}`)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (question.trim()) ask(question.trim())
  }

  return (
    <div className="landing">
      <Header wordmark={false} />
      <section className="hero">
        <div className="hero-text">
          <h1>Understudy</h1>
          <p className="hero-sub">A company brain that knows who can step in.</p>
          <p className="hero-question">
            How many people could you lose before nobody knows how a system works?
          </p>
          <p className="hero-body">
            Counting the people who know a system makes it look safe. Understudy counts the separate
            routes from them to the people who approve its changes, with max-flow on a FalkorDB graph of
            Kubernetes, and an agent rehearses departures on a copy of the graph before they happen.
          </p>
          <div className="hero-actions">
            <a
              className="button button-primary button-large"
              href="/k8s"
              onClick={(e) => {
                e.preventDefault()
                navigate('/k8s')
              }}
            >
              Open the Kubernetes call board
            </a>
          </div>
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
      </section>

      <section className="ask-k8s" aria-labelledby="ask-title">
        <h2 id="ask-title">Now ask about Kubernetes</h2>
        <p>
          79 systems, 9,187 merged pull requests and every OWNERS file since January 2024, in one
          FalkorDB graph. The agent answers only from graph tools and shows each query it ran.
        </p>
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
      </section>

      <footer className="site-foot">
        <p>
          Built solo for Graph Hacks 2026 on FalkorDB. People appear as pseudonyms; the numbers come from
          public Kubernetes history and are a model, not a verdict on anyone.
        </p>
      </footer>
    </div>
  )
}
