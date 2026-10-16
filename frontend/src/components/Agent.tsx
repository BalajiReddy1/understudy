import { type FormEvent, type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from 'react'
import { type Answer, ApiError, type Cites, type Step, ask } from '../api'
import { ms, plural, shortPath, toolLabel } from '../lib/format'

interface Turn {
  id: number
  question: string
  steps: Step[]
  notice: string | null
  answer: Answer | null
  error: string | null
}

interface Props {
  session: string | null
  visitor: string
  suggestions: string[]
  /** A question to ask as soon as the panel opens (from the landing page). */
  initialQuestion?: string | null
  onSession: (id: string) => void
  onChanged: () => void
  onCiteSystem: (name: string) => void
  onCitePerson: (handle: string) => void
}

const KIND: Record<Answer['kind'], string> = {
  path: 'From the graph',
  memory: 'From an earlier session',
  not_in_graph: 'Not in the graph',
  unknown: "Didn't recognise a name",
}
const ACTIONS = new Set(['take_off_stage', 'apply_fix'])

function note(a: Answer): string | null {
  if (a.checked) return null
  if (a.model === null && a.note === 'no model key') {
    return 'Answered without a model: no key is set, so these are the graph tools’ own sentences.'
  }
  if (a.model === null) return 'The free model was busy, so these are the graph tools’ own sentences.'
  return `The model’s wording didn’t match what the tools returned (${a.note}), so this is what the tools said instead.`
}

/** The answer text with every cited system, person and KEP turned into a link. */
function withCites(
  text: string,
  cites: Cites,
  onSystem: (n: string) => void,
  onPerson: (h: string) => void,
): ReactNode[] {
  const tokens = [
    ...cites.systems.map((s) => ({ s, kind: 'system' as const })),
    ...cites.people.map((s) => ({ s, kind: 'person' as const })),
    ...cites.keps.map((k) => ({ s: `KEP-${k.number}`, kind: 'kep' as const, url: k.url, title: k.title })),
  ].sort((a, b) => b.s.length - a.s.length) // longer names first, so a prefix never wins
  if (!tokens.length) return [text]
  const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')
  const re = new RegExp(`(${tokens.map((t) => escape(t.s)).join('|')})(?![\\w/-])`, 'g')
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(re)) {
    const t = tokens.find((x) => x.s === m[1])!
    out.push(text.slice(last, m.index))
    if (t.kind === 'system') {
      out.push(
        <button key={m.index} type="button" className="cite cite-system" onClick={() => onSystem(t.s)}>
          {shortPath(t.s)}
        </button>,
      )
    } else if (t.kind === 'person') {
      out.push(
        <button key={m.index} type="button" className="cite cite-person" onClick={() => onPerson(t.s)}>
          {t.s}
        </button>,
      )
    } else {
      out.push(
        <a key={m.index} className="cite cite-kep" href={t.url} target="_blank" rel="noreferrer" title={t.title ?? undefined}>
          {t.s}
        </a>,
      )
    }
    last = (m.index ?? 0) + m[0].length
  }
  out.push(text.slice(last))
  return out
}

function Trace({ steps }: { steps: Step[] }) {
  const queries = steps.reduce((n, s) => n + s.queries.length, 0)
  const time = steps.reduce((n, s) => n + s.ms, 0)
  if (!steps.length) return null
  return (
    <details className="trace">
      <summary>
        {plural(steps.length, 'tool')} · {plural(queries, 'query', 'queries')} · {ms(time)}
      </summary>
      <ol>
        {steps.map((s, i) => (
          <li key={i} className={`trace-step status-${s.status}`}>
            <details>
              <summary>
                <span className="trace-tool">{toolLabel(s.tool)}</span>
                <code className="trace-args">{JSON.stringify(s.args)}</code>
                <span className="trace-meta">
                  {s.status.replace(/_/g, ' ')} · {ms(s.ms)} · {plural(s.queries.length, 'query', 'queries')}
                </span>
              </summary>
              <p className="trace-summary">{s.summary}</p>
              <ol className="trace-queries">
                {s.queries.slice(0, 12).map((q, j) => (
                  <li key={j}>
                    <code>{q.cypher}</code>
                    <span>
                      {q.graph} · {ms(q.ms)} · {plural(q.rows, 'row')}
                    </span>
                  </li>
                ))}
                {s.queries.length > 12 && <li className="trace-more">and {s.queries.length - 12} more</li>}
              </ol>
            </details>
          </li>
        ))}
      </ol>
    </details>
  )
}

export function Agent(props: Props) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const logRef = useRef<HTMLOListElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const asked = useRef(false)
  const nextId = useRef(1)

  useEffect(() => {
    logRef.current?.lastElementChild?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [turns])

  async function run(question: string) {
    const q = question.trim()
    if (!q || busy) return
    const id = nextId.current++
    const history = turns.flatMap((t) => [
      { role: 'user' as const, content: t.question },
      ...(t.answer ? [{ role: 'assistant' as const, content: t.answer.text }] : []),
    ])
    const update = (fn: (t: Turn) => Turn) => setTurns((all) => all.map((t) => (t.id === id ? fn(t) : t)))
    setTurns((all) => [...all, { id, question: q, steps: [], notice: null, answer: null, error: null }])
    setText('')
    setBusy(true)
    try {
      await ask(q, history.slice(-6), { session: props.session, visitor: props.visitor }, (e) => {
        if (e.type === 'tool') update((t) => ({ ...t, steps: [...t.steps, e] }))
        else if (e.type === 'notice') update((t) => ({ ...t, notice: e.text }))
        else if (e.type === 'error') update((t) => ({ ...t, error: e.text }))
        else if (e.type === 'answer') {
          update((t) => ({ ...t, answer: e, notice: null }))
          if (e.session && e.session !== props.session) props.onSession(e.session)
          if (e.steps.some((s) => ACTIONS.has(s.tool) && s.status === 'ok')) props.onChanged()
        }
      })
    } catch (e) {
      update((t) => ({ ...t, error: e instanceof ApiError ? e.message : 'Something went wrong. Try again.' }))
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  useEffect(() => {
    if (props.initialQuestion && !asked.current) {
      asked.current = true
      void run(props.initialQuestion)
    }
    // Only a new initial question should ask; run changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.initialQuestion])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    void run(text)
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void run(text)
    }
  }

  return (
    <section className="agent" aria-labelledby="agent-title">
      <header className="agent-head">
        <h2 id="agent-title">Ask the graph</h2>
        <p>
          The agent answers only from graph tools. Every system and person it names links to the stage,
          and the trace shows each query it ran.
        </p>
      </header>

      {turns.length === 0 ? (
        <div className="agent-empty">
          <p>Try one of these, or ask your own:</p>
          <ul>
            {props.suggestions.map((s) => (
              <li key={s}>
                <button type="button" className="suggestion" onClick={() => void run(s)}>
                  {s}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <ol className="agent-log" ref={logRef} aria-live="polite">
          {turns.map((t) => (
            <li key={t.id} className="turn">
              <p className="turn-q">{t.question}</p>
              {!t.answer && !t.error && (
                <div className="turn-working">
                  <span className="working-mark" aria-hidden="true" />
                  <span>
                    {t.notice ??
                      (t.steps.length
                        ? `Ran ${t.steps.map((s) => toolLabel(s.tool)).join(', ')}; writing the answer`
                        : 'Choosing which graph tools to run')}
                  </span>
                </div>
              )}
              {t.answer && (
                <div className={`answer kind-${t.answer.kind}`}>
                  <p className="answer-kind">{KIND[t.answer.kind]}</p>
                  <p className="answer-text">
                    {withCites(t.answer.text, t.answer.cites, props.onCiteSystem, props.onCitePerson)}
                  </p>
                  {note(t.answer) && <p className="answer-note">{note(t.answer)}</p>}
                  <Trace steps={t.answer.steps} />
                </div>
              )}
              {t.error && (
                <p className="turn-error" role="alert">
                  {t.error}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}

      <form className="agent-form" onSubmit={submit}>
        <label className="visually-hidden" htmlFor="agent-input">
          Ask a question about Kubernetes
        </label>
        <textarea
          id="agent-input"
          ref={inputRef}
          rows={2}
          maxLength={500}
          placeholder="What happens if someone steps back? Why is a system built this way?"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          disabled={busy}
        />
        <div className="agent-form-row">
          <span className="fine">Enter to ask · Shift+Enter for a new line</span>
          <button type="submit" className="button button-primary" disabled={busy || !text.trim()}>
            {busy ? 'Working…' : 'Ask'}
          </button>
        </div>
      </form>
    </section>
  )
}
