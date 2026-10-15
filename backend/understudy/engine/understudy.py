"""The understudy number: max-flow from a system to the teams that depend on it,
through people who are each a gate of capacity 1. By Menger's theorem that is the
number of people who must leave before no route of knowledge reaches the teams."""

from collections import defaultdict

from falkordb import Graph

FLOW = """
MATCH (s:System {name: $system})<-[:DEPENDS_ON]-(t:Team)
WITH s, collect(t) AS teams
CALL algo.maxFlow({sourceNodes: [s], targetNodes: teams,
                   relationshipTypes: ['KNOWLEDGE'], capacityProperty: 'cap'})
YIELD maxFlow, edges, edgeFlows
RETURN maxFlow, id(s),
       [i IN range(0, size(edges) - 1) WHERE edgeFlows[i] > 0 |
        [id(startNode(edges[i])), id(endNode(edges[i])), edges[i].kind,
         startNode(edges[i]).person]] AS used
"""


def flow(g: Graph, system: str) -> tuple[int, list[str]] | None:
    """(understudy number, carriers) for one system, or None if no team depends on it.

    Carriers are people whose gate carries flow *reachable from the system*. Walking
    from the source matters on 6.0.2, whose per-edge output can include a separate
    circulation; people on that loop carry nothing for this system."""
    rows = g.ro_query(FLOW, {"system": system}).result_set
    if not rows:
        return None
    number, source, used = rows[0]
    out = defaultdict(list)
    for a, b, kind, person in used:
        out[a].append((b, kind, person))
    seen, stack, carriers = {source}, [source], set()
    while stack:
        for b, kind, person in out[stack.pop()]:
            if kind == "gate":
                carriers.add(person)
            if b not in seen:
                seen.add(b)
                stack.append(b)
    return int(number), sorted(carriers)


def understudy(g: Graph, system: str) -> int | None:
    r = flow(g, system)
    return r and r[0]


def all_systems(g: Graph) -> list[str]:
    return [r[0] for r in g.ro_query("MATCH (s:System) RETURN s.name ORDER BY s.name").result_set]
