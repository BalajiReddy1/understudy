import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, type BoardRow, type Rehearsal, api } from '../api'
import { Agent } from '../components/Agent'
import { Board } from '../components/Board'
import { Header } from '../components/Header'
import { StagePanel } from '../components/StagePanel'
import { shortPath } from '../lib/format'
import { setParams } from '../lib/router'
import { read, visitorId, write } from '../lib/storage'

type Tab = 'ask' | 'board' | 'stage'

export function Workspace({ params }: { params: URLSearchParams }) {
  const [session, setSessionState] = useState<string | null>(() => read('understudy.session'))
  const [visitor] = useState(visitorId)
  const [board, setBoard] = useState<BoardRow[] | null>(null)
  const [boardError, setBoardError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const [rehearsal, setRehearsal] = useState<Rehearsal | null>(null)
  const [highlight, setHighlight] = useState<Set<string>>(new Set())
  const [tab, setTab] = useState<Tab>('ask')
  const [example, setExample] = useState<string | null>(null)
  const [initialQuestion] = useState(() => params.get('ask'))

  const setSession = useCallback((id: string | null) => {
    setSessionState(id)
    write('understudy.session', id)
  }, [])

  useEffect(() => {
    if (initialQuestion) setParams({ ask: null })
  }, [initialQuestion])

  useEffect(() => {
    let live = true
    setBoardError(null)
    api
      .board('k8s', session)
      .then((rows) => live && setBoard(rows))
      .catch((e: ApiError) => {
        if (!live) return
        if (session && e.status === 404) setSession(null) // the copy expired: back to the real graph
        else setBoardError(e.message)
      })
    return () => {
      live = false
    }
  }, [session, version, setSession])

  const selected = params.get('system') ?? board?.[0]?.name ?? null

  // A real pseudonym for the suggested "what if" question: the first critical person
  // on the riskiest system.
  useEffect(() => {
    if (!board?.[0] || example) return
    api
      .stage('k8s', board[0].name)
      .then((s) => {
        const p = [...s.approvers, ...s.knowers].find((x) => x.critical)
        if (p) setExample(p.handle)
      })
      .catch(() => {})
  }, [board, example])

  const suggestions = useMemo(() => {
    const top = board?.[0]
    return [
      top ? `How exposed is ${shortPath(top.name)}, and what's the cheapest fix?` : 'Which systems are most at risk?',
      example ? `What breaks if ${example} steps back?` : 'Which systems are most at risk?',
      'Why is pkg/kubelet/cm built the way it is?',
      'How safe is test/e2e/node?',
    ].filter((s, i, all) => all.indexOf(s) === i)
  }, [board, example])

  const select = (name: string) => {
    setParams({ system: name })
    setTab('stage')
  }
  const changed = () => {
    setRehearsal(null)
    setVersion((v) => v + 1)
  }
  const ensureSession = async () => {
    if (session) return session
    const { id } = await api.startSession()
    setSession(id)
    return id
  }
  const rehearse = async (people: string[]) => {
    try {
      setRehearsal(await api.rehearse('k8s', people, session))
    } catch (e) {
      setBoardError((e as Error).message)
    }
  }
  const reset = async () => {
    if (!session) return
    try {
      await api.resetSession(session)
    } catch {
      setSession(null)
    }
    changed()
  }

  return (
    <div className="workspace">
      <Header session={session} onReset={reset} onLeaveCopy={() => (setSession(null), changed())} />
      <nav className="ws-tabs" aria-label="Panels">
        {(['ask', 'board', 'stage'] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            className={tab === t ? 'is-on' : ''}
            aria-pressed={tab === t}
            onClick={() => setTab(t)}
          >
            {t === 'ask' ? 'Ask' : t === 'board' ? 'Call board' : 'Stage'}
          </button>
        ))}
      </nav>
      <main className="ws-grid" data-tab={tab}>
        <div className="ws-board">
          <Board rows={board} error={boardError} selected={selected} rehearsal={rehearsal} onSelect={select} />
        </div>
        <div className="ws-agent">
          <Agent
            session={session}
            visitor={visitor}
            suggestions={suggestions}
            initialQuestion={initialQuestion}
            onSession={setSession}
            onChanged={changed}
            onCiteSystem={select}
            onCitePerson={(h) => {
              setHighlight(new Set([h]))
              setTab('stage')
            }}
          />
        </div>
        <div className="ws-stage">
          {selected ? (
            <StagePanel
              org="k8s"
              system={selected}
              session={session}
              version={version}
              highlight={highlight}
              rehearsal={rehearsal}
              onRehearse={rehearse}
              onClearRehearsal={() => setRehearsal(null)}
              onSelectSystem={select}
              ensureSession={ensureSession}
              onChanged={changed}
            />
          ) : (
            <section className="stage-panel is-loading" aria-busy="true" aria-label="Loading the stage">
              <div className="skeleton skeleton-title" />
              <div className="skeleton skeleton-stage" />
            </section>
          )}
        </div>
      </main>
    </div>
  )
}
