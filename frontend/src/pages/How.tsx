import { type ReactNode, useEffect, useState } from 'react'
import { type Model, api } from '../api'
import { type DocSection, DocPage } from '../components/DocPage'
import { navigate } from '../lib/router'

const REPO = 'https://github.com/BalajiReddy1/understudy/blob/main/backend/understudy'

const SECTIONS: DocSection[] = [
  { id: 'data', title: 'Where the data comes from' },
  { id: 'graph', title: 'The graph' },
  { id: 'number', title: 'How a number is worked out' },
  { id: 'queries', title: 'The queries' },
  { id: 'agent', title: 'The agent' },
  { id: 'engine', title: 'What broke in FalkorDB' },
  { id: 'parts', title: 'How the pieces fit' },
]

const TOOLS: [string, string][] = [
  ['find_system', 'Resolve what you called a system to its exact name'],
  ['understudies', 'How exposed a system is, or the riskiest ones'],
  ['rehearse_departure', 'What drops if people leave, on a copy'],
  ['take_off_stage', 'Make a departure real in your own copy'],
  ['propose_fix', 'Try fixes on copies and rank the ones that help'],
  ['apply_fix', 'Apply one to your own copy'],
  ['who_can_review', 'Who knows a system best, and whether they approve it'],
  ['why', 'The KEPs behind the most work on a system'],
  ['recall', 'What earlier sessions found, with dates'],
]

function count(n: number | undefined): string {
  return n === undefined ? '…' : n.toLocaleString('en')
}

/** One query as it appears in the code: what it does on the left, the Cypher on the right. */
function Query({ title, file, children, cypher }: { title: string; file: string; children: ReactNode; cypher: string }) {
  return (
    <article className="doc-query">
      <div className="doc-query-meta">
        <h3>{title}</h3>
        <p>{children}</p>
        <p className="fine">
          <a href={`${REPO}/${file}`}>{file}</a>
        </p>
      </div>
      <pre>
        <code>{cypher}</code>
      </pre>
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
    <DocPage
      title="How it works"
      lead={
        <p>
          Everything here comes from public Kubernetes history and one FalkorDB graph: what is in the graph,
          how an understudy number is worked out, the queries that do the work, and what broke in the
          database along the way.
        </p>
      }
      aside={<FlowDiagram />}
      sections={SECTIONS}
    >
      <section className="doc-section" id="data" aria-labelledby="data-title">
        <h2 id="data-title">Where the data comes from</h2>
        <div className="doc-pair">
          <p>
            Every merged pull request in kubernetes/kubernetes from January 2024 to October 14, 2026, with its
            files, reviews and comments. Every version of every OWNERS file as it landed on master. The KEPs
            in kubernetes/enhancements. Real GitHub handles are swapped for salted pseudonyms before anything
            is written to the graph, and bots are dropped.
          </p>
          <dl className="doc-facts">
            <div>
              <dt>Merged pull requests</dt>
              <dd>9,187</dd>
            </div>
            <div>
              <dt>Window</dt>
              <dd>Jan 2024 to Oct 2026</dd>
            </div>
            <div>
              <dt>People in the graph</dt>
              <dd>{count(n.Person)}</dd>
            </div>
            <div>
              <dt>Systems</dt>
              <dd>{count(n.System)}</dd>
            </div>
          </dl>
        </div>
      </section>

      <section className="doc-section" id="graph" aria-labelledby="graph-title">
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
              <code>plugin/</code> or <code>staging/</code>, busy enough to stand on its own (20 PRs). Quieter
              ones fold into their parent.
            </dd>
            <dt>
              <code>Team</code> <span className="count">{count(n.Team)}</span>
            </dt>
            <dd>
              The approvers in a system’s nearest OWNERS file, aliases expanded. If nobody listed is active any
              more, approval falls to the next file up, the way it does in practice.
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
              Person to system. Authoring a PR there scores 1, reviewing or commenting 0.5, and old work halves
              every six months. Below 1 doesn’t count.
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
              Saved results: who is on a route for a system, and who alone would lower its number by leaving.
            </dd>
          </dl>
        </div>
      </section>

      <section className="doc-section" id="number" aria-labelledby="number-title">
        <h2 id="number-title">How a number is worked out</h2>
        <div className="doc-pair">
          <div className="doc-prose">
            <p>
              A route from a system to the people who approve its changes is one person (an approver who knows
              the code) or two (someone who knows it, then an approver who works with them). Longer chains
              aren’t allowed. On the real data they wandered through ten or more people and made a few busy
              reviewers look like they carried everything.
            </p>
            <p>
              Each person is split into an in-port and an out-port joined by an edge of capacity 1, so nobody
              can carry more than one route. Max-flow from the system to its teams then counts routes that
              share nobody. By Menger’s theorem, that is also the fewest people who would have to leave before
              no approver can reach anyone who knows the code. That number is the understudy number.
            </p>
            <p>
              Who counts as a knower depends on the system, and FalkorDB’s max-flow follows a single
              relationship type, so every system gets its own: <code>FLOW_1</code>, <code>FLOW_2</code> and so
              on. A rehearsal copies the graph with <code>GRAPH.COPY</code>, takes people off the copy and runs
              max-flow again only for the systems they were on a route for. Nobody else can change a number.
            </p>
          </div>
          <pre className="doc-card">
            <code>
              {`understudy(billing)
  = max-flow from billing to its teams,
    every person a gate of capacity 1
  = 1    every route passes dev

knowers(billing) = 3    ana, ben, cara

rehearse: dev leaves
  billing   1 → 0
  ledger    1 → 0
  auth      3 → 2`}
            </code>
          </pre>
        </div>
      </section>

      <section className="doc-section" id="queries" aria-labelledby="queries-title">
        <h2 id="queries-title">The queries</h2>
        <p>
          Six of the queries that do the work, exactly as they are in the code. The agent never writes Cypher;
          it can only call tools built on queries like these.
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
            Run on a copy made with <code>GRAPH.COPY</code>. Deleting someone’s ports deletes their gate, so
            every route through them is gone.
          </Query>
          <Query title="The call board" file="engine/stage.py" cypher={BOARD}>
            Every system with its number, how many people know it and approve it, and how many are critical.
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

      <section className="doc-section" id="agent" aria-labelledby="agent-title">
        <h2 id="agent-title">The agent</h2>
        <div className="doc-pair">
          <div className="doc-prose">
            <p>
              The agent on the{' '}
              <a href="/k8s" onClick={go('/k8s')}>
                Kubernetes page
              </a>{' '}
              runs on a free open model through Groq and picks from nine fixed tools. Every system or person it
              names is checked against the graph before a tool runs.
            </p>
            <p>
              Before an answer goes out it’s checked against what the tools returned: every system, person, KEP
              and number has to have come from a tool, and sweeping claims like “nobody” need a tool that said
              so. If the wording doesn’t match, you get the tools’ own sentences instead, with a note saying
              why. Every answer shows the tools it ran and each Cypher query. The same tools work from Claude
              Code or Cursor over MCP.
            </p>
          </div>
          <ul className="doc-tools" aria-label="The agent’s tools">
            {TOOLS.map(([name, what]) => (
              <li key={name}>
                <code>{name}</code>
                <span>{what}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="doc-section" id="engine" aria-labelledby="engine-title">
        <h2 id="engine-title">What broke in FalkorDB</h2>
        <p>
          Every number is checked in the tests against networkx and a brute-force search, on FalkorDB 6.0.2
          and 4.22. That’s how these turned up. The first three have minimal repros written up, and the app
          works around all five.
        </p>
        <div className="doc-table-scroll">
          <table className="doc-table doc-findings">
            <thead>
              <tr>
                <th scope="col">What happens</th>
                <th scope="col">Versions</th>
                <th scope="col">What the app does</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <strong>Max-flow comes back too low when capacities differ.</strong> With capacities of 1 and
                  1000 mixed, <code>algo.maxFlow</code> under-counted 8 of 150 random graphs.
                </td>
                <td className="num">6.0.2, 4.22</td>
                <td>Every flow edge has capacity 1, which matched networkx on all 450 test graphs.</td>
              </tr>
              <tr>
                <td>
                  <strong>A property map that reads an earlier variable matches nothing.</strong>{' '}
                  <code>MATCH (x:Port {'{person: a.handle}'})</code> silently returns no rows.
                </td>
                <td className="num">6.0.2</td>
                <td>
                  Passes the value through <code>WITH</code> first.
                </td>
              </tr>
              <tr>
                <td>
                  <strong>A read-only query can crash the server.</strong> One shape of aggregating query makes
                  6.0.2 panic and exit, even through <code>GRAPH.RO_QUERY</code>.
                </td>
                <td className="num">6.0.2</td>
                <td>
                  Every query finishes its aggregation in a <code>WITH</code>, and the agent never writes
                  Cypher.
                </td>
              </tr>
              <tr>
                <td>
                  <strong>Max-flow errors on a relationship type that doesn’t exist yet</strong> instead of
                  returning 0, for a system nobody active knows.
                </td>
                <td className="num">6.0.2, 4.22</td>
                <td>Reads that error as 0.</td>
              </tr>
              <tr>
                <td>
                  <strong>Copies fail when made in parallel</strong> with “could not fork”, because 4.22 copies
                  a graph by forking the server.
                </td>
                <td className="num">4.22</td>
                <td>Retries the copy.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section className="doc-section" id="parts" aria-labelledby="parts-title">
        <h2 id="parts-title">How the pieces fit</h2>
        <ol className="doc-pipeline">
          <li>
            <strong>Pull</strong>
            <span>PRs, OWNERS history and KEPs from GitHub, once, into files.</span>
          </li>
          <li>
            <strong>Derive</strong>
            <span>The org in Python: systems, teams, who knows what, who works with whom.</span>
          </li>
          <li>
            <strong>FalkorDB</strong>
            <span>
              The <code>org</code> graph, a private copy per visitor who changes something, short-lived copies
              for rehearsals and fixes, and a memory graph per visitor.
            </span>
          </li>
          <li>
            <strong>FastAPI</strong>
            <span>The call board, stages, rehearsals and fixes, the agent’s answers, and this site. The same tools run as an MCP server.</span>
          </li>
          <li>
            <strong>React</strong>
            <span>Draws the stage by hand in SVG.</span>
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
    </DocPage>
  )
}

/** A two-person route drawn as the flow layer sees it. */
function FlowDiagram() {
  const box = (y: number, label: string, sub: string, kind: string) => (
    <g className={`flow-node ${kind}`} transform={`translate(0 ${y})`}>
      <rect x={20} y={0} width={220} height={52} rx={10} />
      <text x={130} y={23} textAnchor="middle">
        {label}
      </text>
      <text x={130} y={41} textAnchor="middle" className="flow-sub">
        {sub}
      </text>
    </g>
  )
  const arrow = (y1: number, y2: number, label: string) => (
    <g className="flow-edge">
      <line x1={130} y1={y1} x2={130} y2={y2 - 7} />
      <path d={`M 124 ${y2 - 8} L 130 ${y2} L 136 ${y2 - 8} Z`} />
      <text x={146} y={(y1 + y2) / 2 + 4}>{label}</text>
    </g>
  )
  return (
    <figure className="flow-diagram">
      <svg viewBox="0 0 260 382" role="img" aria-label="One route: billing, then ana's gate, then dev's gate, then the payments team">
        {box(0, 'billing', 'the system', 'is-system')}
        {arrow(52, 110, 'knows')}
        {box(110, 'ana: in → out', 'knows it · capacity 1', 'is-gate')}
        {arrow(162, 220, 'works with')}
        {box(220, 'dev: in → out', 'approves · capacity 1', 'is-gate')}
        {arrow(272, 330, 'member of')}
        {box(330, 'payments', 'the team', 'is-team')}
      </svg>
      <figcaption>
        One route through the flow layer. Each person is a gate of capacity 1, so max-flow counts routes
        that share nobody.
      </figcaption>
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
