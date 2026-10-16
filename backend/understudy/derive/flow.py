"""Write an org into a graph and build the flow layer that max-flow runs on.

An org is a plain dict, the same shape whether it comes from the sample seed or from
the Kubernetes loader:

    teams:        ["payments", ...] or [{"name": ..., "kind": "approvers"}, ...]
    systems:      [{"name": "billing", "teams": ["payments"]}, ...]   teams = DEPENDS_ON
                  optional: "sigs" (owning SIGs), "emeritus_approvers" (a count),
                  "inactive_owners" (OWNERS file whose approvers have all gone quiet)
    people:       [{"handle": "ana", "teams": ["growth"]}, ...]       teams = MEMBER_OF
    knows:        [{"person": "ana", "system": "billing", "score": 1.0}, ...]
    collaborates: [["ana", "dev"], ...]                               undirected pairs

The flow layer. A route from a system to a team that depends on it is either one
person (an approver who knows the system) or two (someone who knows it, then an
approver they work with directly). Each person is a gate of capacity 1, so max-flow
counts routes that share nobody, and by Menger's theorem that is how many people can
leave before no approver has a line to anyone who knows the code.

Longer chains are deliberately impossible. With channels along every collaboration,
routes on the real data wandered through 10+ people (126 of 321), central reviewers
"carried" most systems, and the stage was undrawable. Here a channel only runs from a
knower to an approver who doesn't know the system (one who does routes directly), and
an approver's out-port leads only to the team, so no route can pass through anyone.
Any longer chain could be cut down to its last knower anyway, so no number is lost.

Each system gets its own relationship type (FLOW_<n>, stored as System.flow), since
the channels depend on who knows which system. Rules from testing the engine:
  1. Every flow edge has cap = 1. algo.maxFlow under-counts with mixed capacities,
     and with each person a gate of capacity 1 the answer is the same anyway.
  2. maxFlow takes a single relationship type and rejects duplicate edges, so each
     system's edges share one type (told apart by `kind`) and are built with MERGE.
  3. Outer values go through WITH before an inline property map. On 6.0.2,
     MATCH (x:Port {person: a.handle}) silently matches nothing.
"""

from falkordb import Graph

BATCH = 5000

INDEXES = [
    "CREATE INDEX FOR (p:Person) ON (p.handle)",
    "CREATE INDEX FOR (s:System) ON (s.name)",
    "CREATE INDEX FOR (t:Team) ON (t.name)",
    "CREATE INDEX FOR (p:Port) ON (p.person)",
]

# {T} is the system's own relationship type. Type names can't be query parameters, so
# they're formatted in; they are always FLOW_<n> generated below, never user input.
FLOW_LAYER = [
    # Everyone active who knows the system: a gate, and system -> their in-port.
    """MATCH (s:System {name: $system})<-[k:KNOWS]-(p:Person)
       WHERE p.left_at IS NULL AND k.score >= $min_score
       WITH s, p.handle AS h
       MERGE (i:Port {person: h, side: 'in'})
       MERGE (o:Port {person: h, side: 'out'})
       MERGE (i)-[g:{T} {kind: 'gate'}]->(o) SET g.cap = 1
       MERGE (s)-[e:{T} {kind: 'knows'}]->(i) SET e.cap = 1""",
    # Every active approver of a team that depends on it: a gate, and out-port -> team.
    """MATCH (s:System {name: $system})<-[:DEPENDS_ON]-(t:Team)<-[:MEMBER_OF]-(p:Person)
       WHERE p.left_at IS NULL
       WITH t, p.handle AS h
       MERGE (i:Port {person: h, side: 'in'})
       MERGE (o:Port {person: h, side: 'out'})
       MERGE (i)-[g:{T} {kind: 'gate'}]->(o) SET g.cap = 1
       MERGE (o)-[e:{T} {kind: 'member'}]->(t) SET e.cap = 1""",
    # A knower's out-port -> the in-port of each approver they work with who doesn't
    # know the system themselves (one who does already has a route of their own).
    """MATCH (s:System {name: $system})<-[kk:KNOWS]-(k:Person)-[:COLLABORATES]->(a:Person),
             (a)-[:MEMBER_OF]->(:Team)-[:DEPENDS_ON]->(s)
       WHERE kk.score >= $min_score AND k.left_at IS NULL AND a.left_at IS NULL
       OPTIONAL MATCH (a)-[ka:KNOWS]->(s)
       WITH k, a, ka WHERE ka IS NULL OR ka.score < $min_score
       WITH DISTINCT k.handle AS kh, a.handle AS ah
       MATCH (ko:Port {side: 'out'}) WHERE ko.person = kh
       MATCH (ai:Port {side: 'in'}) WHERE ai.person = ah
       MERGE (ko)-[e:{T} {kind: 'channel'}]->(ai) SET e.cap = 1""",
]


def _batched(g: Graph, query: str, rows: list) -> None:
    for i in range(0, len(rows), BATCH):
        g.query(query, {"rows": rows[i : i + BATCH]})


def write_org(g: Graph, org: dict) -> None:
    for q in INDEXES:
        g.query(q)
    _batched(
        g,
        "UNWIND $rows AS t CREATE (x:Team {name: t.name}) SET x.kind = t.kind",
        [t if isinstance(t, dict) else {"name": t} for t in org["teams"]],
    )
    _batched(
        g,
        """UNWIND $rows AS s CREATE (sys:System {name: s.name})
           SET sys.sigs = s.sigs, sys.emeritus_approvers = s.emeritus_approvers,
               sys.inactive_owners = s.inactive_owners
           WITH sys, s UNWIND s.teams AS tn
           MATCH (t:Team {name: tn}) CREATE (t)-[:DEPENDS_ON]->(sys)""",
        org["systems"],
    )
    _batched(
        g,
        """UNWIND $rows AS p CREATE (x:Person {handle: p.handle})
           SET x.left_at = p.left_at
           WITH x, p UNWIND p.teams AS tn
           MATCH (t:Team {name: tn}) CREATE (x)-[:MEMBER_OF]->(t)""",
        [{"left_at": None, **p} for p in org["people"]],
    )
    _batched(
        g,
        """UNWIND $rows AS k MATCH (p:Person {handle: k.person}), (s:System {name: k.system})
           CREATE (p)-[:KNOWS {score: k.score}]->(s)""",
        org["knows"],
    )
    pairs = {tuple(p) for a, b in org["collaborates"] if a != b for p in ((a, b), (b, a))}
    _batched(
        g,
        """UNWIND $rows AS c MATCH (a:Person {handle: c[0]}), (b:Person {handle: c[1]})
           CREATE (a)-[:COLLABORATES]->(b)""",
        [list(p) for p in sorted(pairs)],
    )


def build_flow_layer(g: Graph, min_score: float = 0.0, systems: list[str] | None = None) -> None:
    """Build the flow edges for `systems` (default: all). A system without a type yet
    gets the next free FLOW_<n>. Everything is MERGEd, so building a system again after
    someone joins a team or a new collaboration adds only the edges that are missing."""
    rows = g.query("MATCH (s:System) RETURN s.name, s.flow ORDER BY s.name").result_set
    taken = [int(t.removeprefix("FLOW_")) for _, t in rows if t]
    n = max(taken, default=-1) + 1
    types = {}
    for name, flow_type in rows:
        if flow_type is None:
            flow_type, n = f"FLOW_{n}", n + 1
            g.query("MATCH (s:System {name: $s}) SET s.flow = $t", {"s": name, "t": flow_type})
        types[name] = flow_type
    for system in systems if systems is not None else list(types):
        for q in FLOW_LAYER:
            g.query(q.replace("{T}", types[system]), {"system": system, "min_score": min_score})
