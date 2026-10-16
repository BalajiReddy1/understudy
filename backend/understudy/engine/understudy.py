"""The understudy number: max-flow from a system to the teams that depend on it,
through people who are each a gate of capacity 1. A route is an approver who knows
the system, or someone who knows it plus an approver they work with (see
derive/flow.py). By Menger's theorem the number is how many people must leave before
no approver has a line to anyone who knows the code."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from falkordb import FalkorDB, Graph
from redis.exceptions import ResponseError

FLOW = """
MATCH (s:System {name: $system})<-[:DEPENDS_ON]-(t:Team)
WITH s, collect(t) AS teams
CALL algo.maxFlow({sourceNodes: [s], targetNodes: teams,
                   relationshipTypes: [s.flow], capacityProperty: 'cap'})
YIELD maxFlow, edges, edgeFlows
RETURN maxFlow, id(s), [t IN teams | id(t)],
       [i IN range(0, size(edges) - 1) WHERE edgeFlows[i] > 0 |
        [id(startNode(edges[i])), id(endNode(edges[i])), edges[i].kind,
         startNode(edges[i]).person]] AS used
"""


@dataclass(frozen=True)
class Flow:
    number: int
    routes: list[list[str]]  # each route: the handles it passes through, knower first

    @property
    def carriers(self) -> list[str]:
        """Everyone on a route. Anyone else can leave without changing the number,
        because these routes stay valid without them and removing people never adds
        a route."""
        return sorted({h for route in self.routes for h in route})


def _routes(source: int, sinks: set[int], used: list) -> list[list[str]]:
    """Split the flow into separate routes from the system to a team.

    Every edge has capacity 1, so each edge carrying flow belongs to exactly one route.
    Searching only from the source also drops any loop the engine returns on the side
    (6.0.2 can return a valid flow that includes a separate circulation)."""
    out = defaultdict(list)
    for a, b, kind, person in used:
        out[a].append((b, kind, person))
    routes = []
    while True:
        # Depth-first search for a source-to-sink path over the edges still unused.
        stack, seen = [(source, [])], {source}
        found = None
        while stack and found is None:
            node, path = stack.pop()
            for i, (b, kind, person) in enumerate(out[node]):
                step = path + [(node, i, kind, person)]
                if b in sinks:
                    found = step
                    break
                if b not in seen:
                    seen.add(b)
                    stack.append((b, step))
        if found is None:
            return routes
        for node, i, _, _ in sorted(found, key=lambda s: -s[1]):
            out[node].pop(i)
        routes.append([person for _, _, kind, person in found if kind == "gate"])


def flow(g: Graph, system: str) -> Flow | None:
    """Number and routes for one system, or None if no team depends on it."""
    try:
        rows = g.ro_query(FLOW, {"system": system}).result_set
    except ResponseError as e:
        # A system nobody knows and nobody active approves has no flow edges, so its
        # relationship type was never created and both engines refuse the call.
        # Its number is simply 0.
        if "relationship" in str(e).lower() and "type" in str(e).lower():
            return Flow(0, [])
        raise
    if not rows:
        return None
    number, source, sinks, used = rows[0]
    return Flow(int(number), _routes(source, set(sinks), used))


def flows(db: FalkorDB, graph: str, systems: list[str], workers: int = 8) -> dict[str, Flow]:
    """flow() for many systems at once, each on its own pooled connection."""
    if not systems:
        return {}
    with ThreadPoolExecutor(max_workers=min(workers, len(systems))) as pool:
        results = pool.map(lambda s: flow(db.select_graph(graph), s), systems)
        return {s: f for s, f in zip(systems, results, strict=True) if f is not None}


def understudy(g: Graph, system: str) -> int | None:
    f = flow(g, system)
    return f and f.number


def all_systems(g: Graph) -> list[str]:
    return [r[0] for r in g.ro_query("MATCH (s:System) RETURN s.name ORDER BY s.name").result_set]


def take_off_stage(g: Graph, people: list[str], when: str) -> None:
    """Remove people from every route: deleting their ports deletes their gates."""
    g.query(
        """MATCH (p:Person) WHERE p.handle IN $h SET p.left_at = $when
           WITH collect(p.handle) AS hs
           MATCH (port:Port) WHERE port.person IN hs DETACH DELETE port""",
        {"h": people, "when": when},
    )
