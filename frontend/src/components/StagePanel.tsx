import { useEffect, useMemo, useState } from 'react'
import { type Fix, type Org, type Proposal, type Rehearsal, type Stage as StageData, api } from '../api'
import { ms, plural, shortHandle, splitPath } from '../lib/format'
import { verdict } from '../lib/verdict'
import { Stage } from './Stage'

interface Props {
  org: Org
  system: string
  session: string | null
  /** Bumped by the workspace whenever the visitor's copy changes. */
  version: number
  highlight: Set<string>
  rehearsal: Rehearsal | null
  onRehearse: (people: string[]) => Promise<void>
  onClearRehearsal: () => void
  onSelectSystem: (name: string) => void
  /** Makes the visitor's own copy if there isn't one yet; returns its id. */
  ensureSession?: () => Promise<string>
  onChanged?: () => void
}

export function StagePanel(props: Props) {
  const { org, system, session, version, highlight, rehearsal } = props
  const [stage, setStage] = useState<StageData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<null | 'rehearse' | 'fixes' | 'apply' | 'depart'>(null)
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [applied, setApplied] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)

  useEffect(() => {
    let live = true
    setError(null)
    api
      .stage(org, system, session)
      .then((s) => live && setStage(s))
      .catch((e: Error) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [org, system, session, version])

  // A new system starts clean: picks and suggestions belong to the one they were made on.
  useEffect(() => {
    setPicked(new Set())
    setProposal(null)
    setApplied(null)
    setShowAll(false)
  }, [system, org])

  const gone = useMemo(() => new Set(rehearsal?.people ?? []), [rehearsal])
  const routes = rehearsal && stage ? (rehearsal.routes[stage.name] ?? stage.routes) : stage?.routes
  const changed = rehearsal?.changes.find((c) => c.system === system)

  if (error) {
    return (
      <section className="stage-panel" aria-labelledby="stage-title">
        <p className="panel-error" role="alert">
          {error}
        </p>
      </section>
    )
  }
  if (!stage || !routes) {
    return (
      <section className="stage-panel is-loading" aria-busy="true" aria-label="Loading the stage">
        <div className="skeleton skeleton-title" />
        <div className="skeleton skeleton-numbers" />
        <div className="skeleton skeleton-stage" />
      </section>
    )
  }

  const knowers = stage.knowers.filter((k) => !k.off_stage && !gone.has(k.handle)).length
  const approvers = stage.approvers.filter((a) => !a.off_stage && !gone.has(a.handle)).length
  const number = routes.length
  const { lead, tail } = splitPath(stage.name)
  const toggle = (h: string) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(h)) next.delete(h)
      else next.add(h)
      return next
    })

  // The people worth rehearsing first: anyone on a route or critical.
  const everyone = [...stage.approvers, ...stage.knowers].filter(
    (p, i, all) => !p.off_stage && all.findIndex((q) => q.handle === p.handle) === i,
  )
  const key = everyone.filter((p) => p.on_route || p.critical)
  const offered = showAll ? everyone : key.length ? key : everyone.slice(0, 8)

  async function rehearseNow() {
    setBusy('rehearse')
    try {
      await props.onRehearse([...picked])
    } finally {
      setBusy(null)
    }
  }

  async function suggest() {
    setBusy('fixes')
    setApplied(null)
    try {
      setProposal(await api.fixes(system, session))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function tryFix(fix: Fix) {
    if (!props.ensureSession) return
    setBusy('apply')
    try {
      const id = await props.ensureSession()
      const { changes } = await api.applyFix(id, system, fix)
      const here = changes.find((c) => c.system === system)
      setApplied(
        here
          ? `Done in your copy: ${tail} went from ${here.before} to ${here.after}${changes.length > 1 ? `, and ${plural(changes.length - 1, 'other system')} rose too` : ''}.`
          : 'Done in your copy.',
      )
      setProposal(null)
      props.onChanged?.()
    } catch (e) {
      setApplied((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function departForReal() {
    if (!props.ensureSession) return
    setBusy('depart')
    try {
      const id = await props.ensureSession()
      await api.depart(id, [...picked])
      setPicked(new Set())
      props.onClearRehearsal()
      props.onChanged?.()
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="stage-panel" aria-labelledby="stage-title">
      <header className="stage-head">
        <h2 id="stage-title" className="stage-title">
          {lead && <span className="stage-title-lead">{lead}</span>}
          <span className="stage-title-tail">{tail}</span>
        </h2>
        <div className="verdict">
          <p className="headcount">
            <span>
              <strong>{knowers}</strong> know it
            </span>
            <span>
              <strong>{approvers}</strong> approve it
            </span>
          </p>
          <p className={`understudy-number${number <= 1 ? ' is-alarm' : ''}`}>
            <span className="numeral" aria-hidden="true">
              {changed ? (
                <>
                  <s>{changed.before}</s>
                  {changed.after}
                </>
              ) : (
                number
              )}
            </span>
            <span className="numeral-label">
              understudy number
              <span className="visually-hidden">: {number}</span>
            </span>
          </p>
        </div>
        <p className="verdict-line">{verdict(stage, routes, gone)}</p>
        {(stage.inactive_owners || stage.emeritus_approvers > 0 || stage.sigs.length > 0) && (
          <p className="stage-meta">
            {stage.sigs.length > 0 && <span>{stage.sigs.join(', ')}</span>}
            {stage.emeritus_approvers > 0 && (
              <span>{plural(stage.emeritus_approvers, 'former approver')} in its OWNERS file</span>
            )}
            {stage.inactive_owners && <span>everyone listed in {stage.inactive_owners} has gone quiet</span>}
          </p>
        )}
      </header>

      <div className="stage-scroll">
        <Stage
          stage={stage}
          routes={routes}
          off={gone}
          selected={picked}
          highlight={highlight}
          onTogglePerson={toggle}
          compactNames={org === 'sample'}
        />
      </div>
      <p className="stage-legend">
        <span className="key key-route">on a route</span>
        <span className="key key-critical">critical: losing them alone lowers the number</span>
        <span className="key key-gone">off stage</span>
      </p>

      <section className="tray" aria-labelledby="tray-title">
        <h3 id="tray-title">Rehearse a departure</h3>
        <p className="tray-help">
          Pick people, then rehearse. The graph is copied and the copy loses them; nothing changes for
          real.
        </p>
        <div className="chips" role="group" aria-label="People to take off stage">
          {offered.map((p) => (
            <button
              key={p.handle}
              type="button"
              className={`chip chip-person${picked.has(p.handle) ? ' is-on' : ''}${p.critical ? ' is-critical' : ''}`}
              aria-pressed={picked.has(p.handle)}
              onClick={() => toggle(p.handle)}
            >
              {org === 'sample' ? p.handle : shortHandle(p.handle)}
              {p.critical && <span className="chip-tag">critical</span>}
            </button>
          ))}
          {everyone.length > offered.length && !showAll && (
            <button type="button" className="chip chip-more" onClick={() => setShowAll(true)}>
              Show all {everyone.length}
            </button>
          )}
        </div>
        <div className="tray-actions">
          <button
            type="button"
            className="button button-primary"
            disabled={picked.size === 0 || busy !== null}
            onClick={rehearseNow}
          >
            {busy === 'rehearse' ? 'Rehearsing…' : picked.size > 1 ? 'Rehearse their departure' : 'Rehearse this departure'}
          </button>
          {rehearsal && (
            <button type="button" className="button button-quiet" onClick={props.onClearRehearsal}>
              Clear rehearsal
            </button>
          )}
          {props.ensureSession && picked.size > 0 && (
            <button type="button" className="button button-quiet" disabled={busy !== null} onClick={departForReal}>
              {busy === 'depart' ? 'Taking them off…' : 'Take them off stage in my copy'}
            </button>
          )}
        </div>

        {rehearsal && (
          <div className="rehearsal-result" aria-live="polite">
            <p>
              {rehearsal.changes.length === 0
                ? `If ${rehearsal.people.join(', ')} left, no number would drop: they carry no routes.`
                : `If ${rehearsal.people.length === 1 ? 'they' : 'they all'} left, ${rehearsal.changes.length} of ${rehearsal.total_systems} systems would drop.`}
            </p>
            {rehearsal.changes.length > 0 && (
              <ul className="change-list">
                {rehearsal.changes.slice(0, 6).map((c) => (
                  <li key={c.system}>
                    <button type="button" className="link" onClick={() => props.onSelectSystem(c.system)}>
                      {splitPath(c.system).tail}
                    </button>
                    <span className={`change${c.after <= 1 ? ' is-alarm' : ''}`}>
                      {c.before} → {c.after}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p className="fine">
              Copied the graph and re-ran {plural(rehearsal.rerun.length, 'flow')} in{' '}
              {ms(rehearsal.timings_ms.total ?? 0)}.
            </p>
          </div>
        )}
      </section>

      {org === 'k8s' && (
        <section className="fixes" aria-labelledby="fixes-title">
          <div className="fixes-head">
            <h3 id="fixes-title">Raise the number</h3>
            <button type="button" className="button button-quiet" disabled={busy !== null} onClick={suggest}>
              {busy === 'fixes' ? 'Trying fixes on copies…' : proposal ? 'Suggest again' : 'Suggest a fix'}
            </button>
          </div>
          {applied && (
            <p className="applied" role="status">
              {applied}
            </p>
          )}
          {proposal && proposal.fixes.length === 0 && <p className="tray-help">{proposal.reason}</p>}
          {proposal && proposal.fixes.length > 0 && (
            <ol className="fix-list">
              {proposal.fixes.map((f) => (
                <li key={`${f.kind}:${f.knower}:${f.approver}`}>
                  <p>{f.summary}</p>
                  <p className="fix-gain">
                    +{f.gain} here
                    {f.also.length > 0 && <> · lifts {plural(f.also.length, 'other system')}</>}
                  </p>
                  {f.owners_diff && <pre className="diff">{f.owners_diff}</pre>}
                  {props.ensureSession && (
                    <button type="button" className="button button-quiet" disabled={busy !== null} onClick={() => tryFix(f)}>
                      {busy === 'apply' ? 'Applying…' : 'Try it in my copy'}
                    </button>
                  )}
                </li>
              ))}
            </ol>
          )}
          {proposal && <p className="fine">Tried {plural(proposal.tried, 'change')}, each on its own copy, in {ms(proposal.timings_ms.total ?? 0)}.</p>}
        </section>
      )}
    </section>
  )
}
