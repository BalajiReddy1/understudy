"""Keep the cached results on a graph current: each system's number, CARRIES (who is
on a route) and CRITICAL (whose departure alone would lower the number).

A rehearsal reads CARRIES to re-run only the systems a departing person carries,
since nobody else's number can move. The stage reads CRITICAL to outline people."""

from falkordb import FalkorDB

from .critical import critical_people
from .understudy import Flow, all_systems, flows

LINK = """UNWIND $rows AS r MATCH (p:Person {handle: r.person}), (s:System {name: r.system})
          MERGE (p)-[:{REL}]->(s)"""


def refresh(
    db: FalkorDB, graph: str, systems: list[str] | None = None, workers: int = 8
) -> dict[str, Flow]:
    """Recompute everything cached for `systems` (default: all of them)."""
    g = db.select_graph(graph)
    results = flows(db, graph, systems if systems is not None else all_systems(g), workers)
    names = list(results)
    g.query("MATCH ()-[c:CARRIES]->(s:System) WHERE s.name IN $s DELETE c", {"s": names})
    g.query("MATCH ()-[c:CRITICAL]->(s:System) WHERE s.name IN $s DELETE c", {"s": names})
    g.query(
        "UNWIND $rows AS r MATCH (s:System {name: r.name}) SET s.understudy = r.number",
        {"rows": [{"name": s, "number": f.number} for s, f in results.items()]},
    )
    g.query(
        LINK.replace("{REL}", "CARRIES"),
        {"rows": [{"person": p, "system": s} for s, f in results.items() for p in f.carriers]},
    )
    critical = critical_people(db, graph, results)
    g.query(
        LINK.replace("{REL}", "CRITICAL"),
        {"rows": [{"person": p, "system": s} for s, ps in critical.items() for p in ps]},
    )
    return results
