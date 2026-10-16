import { type ReactNode, useEffect, useState } from 'react'
import { type Model, api } from '../api'
import { Header } from '../components/Header'
import { navigate } from '../lib/router'

const REPO = 'https://github.com/BalajiReddy1/understudy/blob/main/backend/understudy'

function count(n: number | undefined): string {
  return n === undefined ? '…' : n.toLocaleString('en')
}

/** One query as it appears in the code, with where to find it. */
function Query({ title, file, children, cypher }: { title: string; file: string; children: ReactNode; cypher: string }) {
  return (
    <article className="doc-query">
      <h3>{title}</h3>
      <p>{children}</p>
      <pre>
        <code>{cypher}</code>
      </pre>
      <p className="fine">
        <a href={`${REPO}/${file}`}>{file}</a>
      </p>
    </article>
  )
}

export function How() {
  const [model, setModel] = useState<Model | null>(null)
  useEffect(() => {
    api
      .model()
      .then(setModel)
      .catch(() => setModel(null))
  }, [])
  const n = model?.nodes ?? {}
  const e = model?.edges ?? {}
  const go = (to: string) => (ev: React.MouseEvent) => {
    ev.preventDefault()
    navigate(to)
  }

  return (
    <div className="doc-page">
      <Header />
      <main className="doc">
        <header className="doc-head">
          <h1>How it works</h1>
          <div className="doc-answer">
            <p>
              Everything here comes from public Kubernetes history and one FalkorDB graph. This page
              covers what’s in the graph, how an understudy number is worked out, the queries that do the
              work, and what broke in the database along the way.
            </p>
          </div>
        </header>

        <section className="doc-section" aria-labelledby="data-title">
          <h2 id="data-title">Where the data comes from</h2>
          <p>
            Every merged pull request in kubernetes/kubernetes from January 2024 to October 14, 2026, with
            its files, reviews and comments. Every version of every OWNERS file as it landed on master. The
            KEPs in kubernetes/enhancements. Real GitHub handles are swapped for salted pseudonyms before
            anything is written to the graph, and bots are dropped.
          </p>
        </section>

        <section className="doc-section" aria-labelledby="graph-title">
          <h2 id="graph-title">The graph</h2>
          <p>The counts are live from the graph this site runs on.</p>
          <div className="doc-model">
            <dl className="doc-defs">
              <dt>
                <code>Person</code> <span className="count">{count(n.Person)}</span>
              </dt>
              <dd>Someone active in the last 18 months, as a pseudonym.</dd>
              <dt>
                <code>System</code> <span className="count">{count(n.System)}</span>
              </dt>
              <dd>
                A directory with its own OWNERS file under <code>pkg/</code>, <code>cmd/</code>,{' '}
                <code>plugin/</code> or <code>staging/</code>, busy enough to stand on its own (20 PRs).
                Quieter ones fold into their parent.
              </dd>
              <dt>
                <code>Team</code> <span className="count">{count(n.Team)}</span>
              </dt>
              <dd>
                The approvers in a system’s nearest OWNERS file, aliases expanded. If nobody listed is active
                any more, approval falls to the next file up, the way it does in practice.
              </dd>
              <dt>
                <code>Decision</code> <span className="count">{count(n.Decision)}</span>
              </dt>
              <dd>A KEP that merged PRs in a system point to.</dd>
              <dt>
                <code>Port</code> <span className="count">{count(n.Port)}</span>
              </dt>
              <dd>Half of a person in the flow layer, explained below.</dd>
            </dl>
            <dl className="doc-defs">
              <dt>
                <code>KNOWS</code> <span className="count">{count(e.KNOWS)}</span>
              </dt>
              <dd>
                Person to system. Authoring a PR there scores 1, reviewing or commenting 0.5, and old work
                halves every six months. Below 1 doesn’t count.
              </dd>
              <dt>
                <code>COLLABORATES</code> <span className="count">{count(e.COLLABORATES)}</span>
              </dt>
              <dd>
                Two people who have been on each other’s PRs, one as author and the other reviewing or
                commenting, at least twice in the last year. Stored both ways.
              </dd>
              <dt>
                <code>MEMBER_OF</code> <span className="count">{count(e.MEMBER_OF)}</span>
              </dt>
              <dd>An approver on a team, if they have 3 or more PRs in the last 18 months.</dd>
              <dt>
                <code>DEPENDS_ON</code> <span className="count">{count(e.DEPENDS_ON)}</span>
              </dt>
              <dd>Team to the system whose changes it approves.</dd>
              <dt>
                <code>SHAPED</code> <span className="count">{count(e.SHAPED)}</span>
              </dt>
              <dd>KEP to system, with how many PRs link them.</dd>
              <dt>
                <code>FLOW_n</code>{' '}
                <span className="count">
                  {count(model?.flow.edges)} in {count(model?.flow.types)} types
                </span>
              </dt>
              <dd>The flow layer, one relationship type per system.</dd>
              <dt>
                <code>CARRIES</code> <span className="count">{count(e.CARRIES)}</span>,{' '}
                <code>CRITICAL</code> <span className="count">{count(e.CRITICAL)}</span>
              </dt>
              <dd>
                Saved results: who is on a route for a system, and who alone would lower its number by
                leaving.
              </dd>
            </dl>
          </div>
        </section>

        <section className="doc-section" aria-labelledby="number-title">
          <h2 id="number-title">How a number is worked out</h2>
          <p>
            A route from a system to the people who approve its changes is one person (an approver who
            knows the code) or two (someone who knows it, then an approver who works with them). Longer
            chains aren’t allowed. On the real data they wandered through ten or more people and made a
            few busy reviewers look like they carried everything.
          </p>
          <p>
            Each person is split into an in-port and an out-port joined by an edge of capacity 1, so
            nobody can carry more than one route. Max-flow from the system to its teams then counts routes
            that share nobody. By Menger’s theorem, that is also the fewest people who would have to leave
            before no approver can reach anyone who knows the code. That number is the understudy number.
          </p>
          <FlowDiagram />
          <p>
            Who counts as a knower depends on the system, and FalkorDB’s max-flow follows a single
            relationship type, so every system gets its own: <code>FLOW_1</code>, <code>FLOW_2</code> and
            so on. A rehearsal copies the graph with <code>GRAPH.COPY</code>, takes people off the copy and
            runs max-flow again only for the systems they were on a route for. Nobody else can change a
            number. That takes about a tenth of a second.
          </p>
        </section>

        <section className="doc-section" aria-labelledby="queries-title">
          <h2 id="queries-title">The queries</h2>
          <p>
            Six of the queries that do the work, exactly as they are in the code. The agent never writes
            Cypher; it can only call tools built on queries like these.
          </p>
          <div className="doc-queries">
            <Query title="Gates for everyone who knows a system" file="derive/flow.py" cypher={GATES}>
              Each person becomes an in-port and an out-port joined by a gate of capacity 1. The type name{' '}
              <code>{'{T}'}</code> is the system’s own <code>FLOW_n</code>.
            </Query>
            <Query title="The understudy number" file="engine/understudy.py" cypher={FLOW}>
              Max-flow from the system to every team that approves it, and the edges that carry flow, so the
              routes can be drawn.
            </Query>
            <Query title="Taking someone off stage" file="engine/understudy.py" cypher={OFF_STAGE}>
              Run on a copy made with <code>GRAPH.COPY</code>. Deleting someone’s ports deletes their gate,
              so every route through them is gone.
            </Query>
            <Query title="The call board" file="engine/stage.py" cypher={BOARD}>
              Every system with its number, how many people know it and approve it, and how many are
              critical.
            </Query>
            <Query title="Trying a pairing as a fix" file="engine/fixes.py" cypher={PAIR}>
              Run on its own copy. If the number goes up there, the pairing is offered; if not, the copy is
              thrown away.
            </Query>
            <Query title="Why a system is built the way it is" file="agent/tools.py" cypher={WHY}>
              The KEPs behind the most work on a system, for the agent’s <code>why</code> tool.
            </Query>
          </div>
        </section>

        <section className="doc-section" aria-labelledby="agent-title">
          <h2 id="agent-title">The agent</h2>
          <p>
            The agent on the{' '}
            <a href="/k8s" onClick={go('/k8s')}>
              Kubernetes page
            </a>{' '}
            runs on a free open model through Groq. It picks from nine fixed tools: find a system, how
            exposed it is, rehearse a departure, take people off stage in your copy, suggest a fix, apply
            one, who can review, why it’s built that way, and what earlier sessions found. Every system or
            person it names is checked against the graph before a tool runs.
          </p>
          <p>
            Before an answer goes out it’s checked against what the tools returned: every system, person,
            KEP and number has to have come from a tool, and sweeping claims like “nobody” need a tool that
            said so. If the wording doesn’t match, you get the tools’ own sentences instead, with a note
            saying why. Every answer shows the tools it ran and each Cypher query. The same tools work from
            Claude Code or Cursor over MCP.
          </p>
        </section>

        <section className="doc-section" aria-labelledby="engine-title">
          <h2 id="engine-title">What broke in FalkorDB</h2>
          <p>
            Every number is checked in the tests against networkx and a brute-force search, on FalkorDB
            6.0.2 and 4.22. That’s how these turned up. The first three have minimal repros written up,
            and the app works around all five.
          </p>
          <dl className="doc-defs doc-findings">
            <dt>Max-flow comes back too low when capacities differ</dt>
            <dd>
              With capacities of 1 and 1000 mixed in one graph, <code>algo.maxFlow</code> under-counted 8 of
              150 random graphs on both versions. With every capacity set to 1 it matched networkx on all
              450. Every flow edge here has capacity 1, which gives the same answer for this model.
            </dd>
            <dt>A property map that reads an earlier variable matches nothing (6.0.2)</dt>
            <dd>
              <code>MATCH (x:Port {'{person: a.handle}'})</code> silently returns no rows. Passing the value
              through <code>WITH</code> first works.
            </dd>
            <dt>A read-only query can crash the server (6.0.2)</dt>
            <dd>
              One shape of aggregating query makes 6.0.2 panic and exit, even through{' '}
              <code>GRAPH.RO_QUERY</code>. 4.22 answers it correctly. Every query here finishes its
              aggregation in a <code>WITH</code>, and since the agent never writes Cypher, visitors can’t
              send it one.
            </dd>
            <dt>Max-flow errors on a relationship type that doesn’t exist yet</dt>
            <dd>
              A system nobody active knows has no flow edges, so its type was never created and both versions
              refuse the call instead of returning 0. The app reads that error as 0.
            </dd>
            <dt>Copies fail when made in parallel (4.22)</dt>
            <dd>
              4.22 copies a graph by forking the server, and only one fork can run at a time, so parallel{' '}
              <code>GRAPH.COPY</code> calls fail with “could not fork”. They’re retried.
            </dd>
          </dl>
        </section>

        <section className="doc-section" aria-labelledby="parts-title">
          <h2 id="parts-title">How the pieces fit</h2>
          <ol className="doc-pipeline">
            <li>
              <strong>Pull</strong> PRs, OWNERS history and KEPs from GitHub once, into files.
            </li>
            <li>
              <strong>Derive</strong> the org in Python: systems, teams, who knows what, who works with
              whom.
            </li>
            <li>
              <strong>FalkorDB</strong> holds the <code>org</code> graph, a private copy for each visitor
              who changes something (gone after two hours), short-lived copies for rehearsals and fixes,
              and a small memory graph per visitor for the agent.
            </li>
            <li>
              <strong>FastAPI</strong> serves the call board, stages, rehearsals and fixes, streams the
              agent’s answers, and serves this site. The same tools run as an MCP server.
            </li>
            <li>
              <strong>React</strong> draws the stage by hand in SVG.
            </li>
          </ol>
          <p>
            Does the number predict anything? That’s on{' '}
            <a href="/proof" onClick={go('/proof')}>
              the proof page
            </a>
            , with what didn’t work.
          </p>
        </section>
      </main>
      <footer className="site-foot">
        <p>Built solo for Graph Hacks 2026 on FalkorDB.</p>
      </footer>
    </div>
  )
}

/** A two-person route drawn as the flow layer sees it. */
function FlowDiagram() {
  const box = (x: number, label: string, sub: string, kind: string) => (
    <g className={`flow-node ${kind}`} transform={`translate(${x} 0)`}>
      <rect x={0} y={22} width={118} height={44} rx={8} />
      <text x={59} y={42} textAnchor="middle">
        {label}
      </text>
      <text x={59} y={58} textAnchor="middle" className="flow-sub">
        {sub}
      </text>
    </g>
  )
  const arrow = (x1: number, x2: number, label: string) => (
    <g className="flow-edge">
      <line x1={x1} y1={44} x2={x2 - 6} y2={44} />
      <path d={`M ${x2 - 7} 39 L ${x2} 44 L ${x2 - 7} 49 Z`} />
      <text x={(x1 + x2) / 2} y={84} textAnchor="middle">
        {label}
      </text>
    </g>
  )
  return (
    <figure className="flow-diagram">
      <svg viewBox="0 0 720 96" role="img" aria-label="A route: system, then a knower's gate, then an approver's gate, then the team">
        {box(0, 'system', 'billing', 'is-system')}
        {arrow(118, 150, 'knows')}
        {box(150, 'in → out', 'ana, cap 1', 'is-gate')}
        {arrow(268, 300, 'channel')}
        {box(300, 'in → out', 'dev, cap 1', 'is-gate')}
        {arrow(418, 450, 'member')}
        {box(450, 'team', 'payments', 'is-team')}
        <text x={600} y={42} className="flow-note">
          one route,
        </text>
        <text x={600} y={58} className="flow-note">
          through two people
        </text>
      </svg>
    </figure>
  )
}

const GATES = `MATCH (s:System {name: $system})<-[k:KNOWS]-(p:Person)
WHERE p.left_at IS NULL AND k.score >= $min_score
WITH s, p.handle AS h
MERGE (i:Port {person: h, side: 'in'})
MERGE (o:Port {person: h, side: 'out'})
MERGE (i)-[g:{T} {kind: 'gate'}]->(o) SET g.cap = 1
MERGE (s)-[e:{T} {kind: 'knows'}]->(i) SET e.cap = 1`

const FLOW = `MATCH (s:System {name: $system})<-[:DEPENDS_ON]-(t:Team)
WITH s, collect(t) AS teams
CALL algo.maxFlow({sourceNodes: [s], targetNodes: teams,
                   relationshipTypes: [s.flow], capacityProperty: 'cap'})
YIELD maxFlow, edges, edgeFlows
RETURN maxFlow, id(s), [t IN teams | id(t)],
       [i IN range(0, size(edges) - 1) WHERE edgeFlows[i] > 0 |
        [id(startNode(edges[i])), id(endNode(edges[i])), edges[i].kind,
         startNode(edges[i]).person]] AS used`

const OFF_STAGE = `MATCH (p:Person) WHERE p.handle IN $h SET p.left_at = $when
WITH collect(p.handle) AS hs
MATCH (port:Port) WHERE port.person IN hs DETACH DELETE port`

const BOARD = `MATCH (s:System)<-[:DEPENDS_ON]-(t:Team)
OPTIONAL MATCH (t)<-[:MEMBER_OF]-(a:Person) WHERE a.left_at IS NULL
WITH s, collect(DISTINCT t.name) AS teams, count(DISTINCT a) AS approvers
OPTIONAL MATCH (s)<-[:KNOWS]-(k:Person) WHERE k.left_at IS NULL
WITH s, teams, approvers, count(DISTINCT k) AS knowers
OPTIONAL MATCH (s)<-[:CRITICAL]-(c:Person)
WITH s, teams, approvers, knowers, count(DISTINCT c) AS critical
RETURN s.name, s.understudy, knowers, approvers, critical, teams,
       s.sigs, s.emeritus_approvers, s.inactive_owners`

const PAIR = `MATCH (a:Person {handle: $a}), (b:Person {handle: $b})
MERGE (a)-[:COLLABORATES]->(b) MERGE (b)-[:COLLABORATES]->(a)`

const WHY = `MATCH (d:Decision)-[r:SHAPED]->(:System {name: $s})
RETURN d.number, d.title, d.stage, d.status, d.url, d.approvers,
       d.approvers_total, r.prs
ORDER BY r.prs DESC, d.number LIMIT 5`
