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

Flow layer rules, from testing the engine before the event:
  1. Every KNOWLEDGE edge has cap = 1. algo.maxFlow under-counts with mixed capacities,
     and with each person a gate of capacity 1 the answer is the same anyway.
  2. One relationship type (KNOWLEDGE, told apart by `kind`), built with MERGE, because
     maxFlow takes a single type and rejects duplicate edges.
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

FLOW_LAYER = [
    # A gate per active person: in-port -> out-port, capacity 1.
    """MATCH (p:Person) WHERE p.left_at IS NULL
       WITH p.handle AS h
       MERGE (i:Port {person: h, side: 'in'})
       MERGE (o:Port {person: h, side: 'out'})
       MERGE (i)-[g:KNOWLEDGE {kind: 'gate'}]->(o)
       SET g.cap = 1""",
    # System -> in-port of everyone who knows it.
    """MATCH (p:Person)-[k:KNOWS]->(s:System) WHERE k.score >= $min_score
       WITH s, p.handle AS h
       MATCH (i:Port {side: 'in'}) WHERE i.person = h
       MERGE (s)-[e:KNOWLEDGE {kind: 'knows'}]->(i)
       SET e.cap = 1""",
    # Out-port -> in-port along every collaboration (stored both ways already).
    """MATCH (a:Person)-[:COLLABORATES]->(b:Person)
       WITH a.handle AS ah, b.handle AS bh
       MATCH (ao:Port {side: 'out'}) WHERE ao.person = ah
       MATCH (bi:Port {side: 'in'}) WHERE bi.person = bh
       MERGE (ao)-[e:KNOWLEDGE {kind: 'channel'}]->(bi)
       SET e.cap = 1""",
    # Out-port -> every team the person is a member of.
    """MATCH (p:Person)-[:MEMBER_OF]->(t:Team)
       WITH t, p.handle AS h
       MATCH (o:Port {side: 'out'}) WHERE o.person = h
       MERGE (o)-[e:KNOWLEDGE {kind: 'member'}]->(t)
       SET e.cap = 1""",
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


def build_flow_layer(g: Graph, min_score: float = 0.0) -> None:
    for q in FLOW_LAYER:
        g.query(q, {"min_score": min_score})
