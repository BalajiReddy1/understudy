"""The understudy number: max-flow from a system to the teams that depend on it,
through people who are each a gate of capacity 1. By Menger's theorem that is the
number of people who must leave before no route of knowledge reaches the teams."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from falkordb import FalkorDB, Graph

FLOW = """
MATCH (s:System {name: $system})<-[:DEPENDS_ON]-(t:Team)
WITH s, collect(t) AS teams
CALL algo.maxFlow({sourceNodes: [s], targetNodes: teams,
                   relationshipTypes: ['KNOWLEDGE'], capacityProperty: 'cap'})
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
    rows = g.ro_query(FLOW, {"system": system}).result_set
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


def refresh(db: FalkorDB, graph: str, workers: int = 8) -> dict[str, Flow]:
    """Recompute every system's number and rebuild the CARRIES cache.

    CARRIES records who is on a route for each system. A rehearsal reads it to re-run
    only the systems a departing person carries, since nobody else's number can move."""
    g = db.select_graph(graph)
    results = flows(db, graph, all_systems(g), workers)
    g.query("MATCH ()-[c:CARRIES]->() DELETE c")
    g.query(
        "UNWIND $rows AS r MATCH (s:System {name: r.name}) SET s.understudy = r.number",
        {"rows": [{"name": s, "number": f.number} for s, f in results.items()]},
    )
    g.query(
        """UNWIND $rows AS r MATCH (p:Person {handle: r.person}), (s:System {name: r.system})
           MERGE (p)-[:CARRIES]->(s)""",
        {"rows": [{"person": p, "system": s} for s, f in results.items() for p in f.carriers]},
    )
    return results
