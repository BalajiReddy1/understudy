import { useEffect, useState } from 'react'
import { type Proof as ProofData, api } from '../api'
import { Header } from '../components/Header'
import { ProofChart } from '../components/ProofChart'
import { plural } from '../lib/format'
import { facts, month, pace, range, signed, verdict } from '../lib/proof'

const CODE = 'https://github.com/BalajiReddy1/understudy/blob/main/backend/understudy/backtest.py'

export function Proof() {
  const [data, setData] = useState<ProofData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .proof()
      .then(setData)
      .catch((e: Error) => setError(e.message))
  }, [])

  return (
    <div className="doc-page">
      <Header />
      <main className="doc">
        <header className="doc-head">
          <h1>Does the number mean anything?</h1>
          {data ? (
            <Answer data={data} />
          ) : error ? (
            <p className="panel-error" role="alert">
              {error}
            </p>
          ) : (
            <div className="skeleton skeleton-numbers" aria-busy="true" aria-label="Loading the backtest" />
          )}
        </header>
        {data && <Body data={data} />}
      </main>
      <footer className="site-foot">
        <p>
          People appear as pseudonyms. A departure here means someone stepped back from an OWNERS file,
          which is a normal part of open source, not something anyone did wrong.
        </p>
      </footer>
    </div>
  )
}

function Answer({ data }: { data: ProofData }) {
  const s = data.summary
  const f = facts(data)
  const v = verdict(s.rho.understudy)
  const noLow = s.groups.low.n === 0
  return (
    <div className="doc-answer">
      {v === 'unproven' && (
        <p>
          <strong>Not yet.</strong> I went back through every departure in the Kubernetes OWNERS
          history since {month(data.data.first_merge)} to see whether reviews slowed down more where
          the understudy number said they would. Only {f.measured} of {f.departures} departures could
          be measured, across {s.systems} systems, and the result could just as easily be noise.
          {noLow &&
            ' None of those departures left a measurable system at 1 or below, so the test never reached the case the number is about.'}
        </p>
      )}
      {v === 'supported' && (
        <p>
          <strong>In this data, yes.</strong> Reviews slowed down more where fewer separate routes were
          left (ρ = {signed(s.rho.understudy.value)}, {range(s.rho.understudy)}), against{' '}
          {signed(s.rho.knowers.value)} for a plain headcount. It is still only {f.measured} departures,
          so it is a first sign, not a law.
        </p>
      )}
      {v === 'contradicted' && (
        <p>
          <strong>In this data, no.</strong> Reviews slowed down less, not more, where fewer separate
          routes were left (ρ = {signed(s.rho.understudy.value)}, {range(s.rho.understudy)}). It is only{' '}
          {f.measured} departures, but that is the opposite of what the number claims.
        </p>
      )}
    </div>
  )
}

function Body({ data }: { data: ProofData }) {
  const s = data.summary
  const f = facts(data)
  const used = data.units.filter((u) => u.reason === null)
  const noLow = s.groups.low.n === 0
  const sorted = [...data.units].sort(
    (a, b) => Number(a.reason !== null) - Number(b.reason !== null) || (b.slowdown ?? 0) - (a.slowdown ?? 0),
  )

  return (
    <>
      <section className="doc-figures" aria-label="The result in four numbers">
        <div>
          <p className="figure">
            {f.measured}
            <span> of {f.departures}</span>
          </p>
          <p>departures could be measured</p>
        </div>
        <div>
          <p className="figure">{s.systems}</p>
          <p>systems they knew or approved, with enough PRs</p>
        </div>
        <div>
          <p className="figure">{signed(s.rho.understudy.value)}</p>
          <p>
            ρ for the understudy number
            <br />
            <span className="fine">95%: {range(s.rho.understudy)}</span>
          </p>
        </div>
        <div>
          <p className="figure">{signed(s.rho.knowers.value)}</p>
          <p>
            ρ for a plain headcount
            <br />
            <span className="fine">95%: {range(s.rho.knowers)}</span>
          </p>
        </div>
      </section>

      <section className="doc-section" aria-labelledby="chart-title">
        <h2 id="chart-title">What happened after each departure</h2>
        <p>
          Each dot is one system the person knew or approved. Up means the first response to a PR got
          slower after they left, compared with how the whole project changed over the same months; the
          darker line at 1× means no difference. If the number works, dots should sit higher on the left
          of the first chart than on the right (a negative ρ).
        </p>
        <ProofChart units={used} rho={s.rho} />
      </section>

      <section className="doc-section" aria-labelledby="wrong-title">
        <h2 id="wrong-title">What didn’t work</h2>
        <ol className="doc-list">
          {noLow && (
            <li>
              <strong>No departure hit a fragile system.</strong> Of the {f.systems.touched} systems these
              people knew or approved, the understudy number dropped for {f.lowered.touched}, and{' '}
              {f.lowered.measured === 0 ? 'none' : f.lowered.measured} of those had enough PRs to measure.
              So the comparison I planned, systems left at 1 or below against the rest, has nothing on
              one side. What the chart can show is whether systems with more routes ride out any
              departure better, which is a weaker question.
            </li>
          )}
          <li>
            <strong>Most departures can’t be used.</strong> Of {f.departures}:
            <ul>
              {f.dropped.map((d) => (
                <li key={d.reason}>
                  <span className="count">{d.count}</span>: {d.reason}
                </li>
              ))}
              {f.kept > f.measured && (
                <li>
                  <span className="count">{f.kept - f.measured}</span>: passed every rule but only touched
                  systems with too few PRs to measure
                </li>
              )}
            </ul>
          </li>
          <li>
            <strong>One departure is most of the data.</strong> {f.largest.systems} of the {s.systems}{' '}
            measured systems come from one person. The intervals come from resampling whole departures, so
            those count as one piece of evidence, not {f.largest.systems}. That is the main reason the
            intervals are so wide, and treating systems as independent would have made them look
            narrower than they really are.
          </li>
          <li>
            <strong>Only merged PRs were pulled.</strong> A PR that stalled and was never merged doesn’t
            show up at all, and that is exactly the kind of slowdown this is looking for.
          </li>
          <li>
            <strong>The history is short.</strong> The pull starts in {month(data.data.first_merge)}, so
            graphs rebuilt for early departures see less than the 18 months of work the model normally
            uses.
          </li>
          <li>
            <strong>It’s observational.</strong> Release freezes, holidays and people changing jobs all
            move review times. Dividing by the project’s own change takes some of that out, not all of it.
          </li>
        </ol>
      </section>

      <section className="doc-section" aria-labelledby="settle-title">
        <h2 id="settle-title">What would settle it</h2>
        <p>
          More departures, and especially ones from systems sitting at 1. Kubernetes history goes back to
          2014; pulling all of it is free, it just takes days against GitHub’s rate limits. Other big
          projects that use OWNERS files would work the same way.
        </p>
      </section>

      <section className="doc-section" aria-labelledby="method-title">
        <h2 id="method-title">How it was measured</h2>
        <p>The method was written down before the first run and hasn’t changed since.</p>
        <dl className="doc-defs">
          <dt>Departures</dt>
          <dd>
            Someone moved to emeritus in an OWNERS file, or dropped from every alias for good. They count
            if they had at least {data.method.min_active_prs} PRs in the 18 months before their last
            activity and none in the {data.method.window_days} days after. The departure date is that last
            activity, because the OWNERS change often comes months later.
          </dd>
          <dt>The graph as it stood</dt>
          <dd>
            For each departure date, the org graph is rebuilt from the data up to that day, loaded into
            FalkorDB, every understudy number worked out, and then the departure is rehearsed on a copy.
            Every system the person knew or approved is one dot.
          </dd>
          <dt>What’s measured</dt>
          <dd>
            Hours from a PR being opened to the first review or comment by someone other than its author
            (bots don’t count), as a median over the PRs touching the system, {data.method.window_days}{' '}
            days after against {data.method.window_days} days before. That ratio is then divided by the
            same ratio for every PR in the project.
          </dd>
          <dt>The rules</dt>
          <dd>
            A PR counts if it merged within {data.method.grace_days} days of its window closing, on both
            sides. A system needs {data.method.min_prs} PRs in each window. PRs touching more than 8
            systems are left out, as they are when the graph is built.
          </dd>
          <dt>The statistics</dt>
          <dd>
            Spearman’s ρ between each measure and the slowdown. The 95% intervals come from{' '}
            {Number(data.method.resamples).toLocaleString('en')} resamples of whole departures with a fixed
            seed, so anyone rerunning it gets the same numbers.
          </dd>
        </dl>
      </section>

      <section className="doc-section" aria-labelledby="table-title">
        <h2 id="table-title">Every system</h2>
        <details className="doc-table-wrap">
          <summary>Show all {plural(data.units.length, 'row')}</summary>
          <div className="doc-table-scroll">
            <table className="doc-table">
              <thead>
                <tr>
                  <th scope="col">Left</th>
                  <th scope="col">System</th>
                  <th scope="col">Number</th>
                  <th scope="col">Know it</th>
                  <th scope="col">PRs</th>
                  <th scope="col">First response</th>
                  <th scope="col">Against the project</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((u) => (
                  <tr key={`${u.person}|${u.system}`} className={u.reason ? 'is-out' : undefined}>
                    <td>{month(u.left_at)}</td>
                    <td className="mono">{u.system}</td>
                    <td className="num">
                      {u.understudy_before} → {u.understudy_after}
                    </td>
                    <td className="num">{u.knowers_after}</td>
                    <td className="num">
                      {u.prs_before} / {u.prs_after}
                    </td>
                    <td className="num">
                      {u.hours_before ?? 'n/a'} h → {u.hours_after ?? 'n/a'} h
                    </td>
                    <td>{u.slowdown === null ? u.reason : pace(u.slowdown)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
        <p className="doc-links">
          <a href="/api/proof">The raw numbers (JSON)</a> · <a href={CODE}>The code</a> · Run on{' '}
          {data.generated_at.slice(0, 10)} over {data.data.prs.toLocaleString('en')} merged PRs
        </p>
      </section>
    </>
  )
}
