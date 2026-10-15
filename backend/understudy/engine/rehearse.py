"""Rehearse a departure: copy the graph, take people off stage on the copy, re-run
only the flows they carried, and compare. The graph being rehearsed never changes."""

import time
import uuid
from dataclasses import dataclass, field

from falkordb import FalkorDB

from .understudy import Flow, flows


class UnknownPeople(ValueError):
    def __init__(self, handles: list[str]):
        super().__init__(f"not in the graph: {', '.join(handles)}")
        self.handles = handles


@dataclass
class Change:
    system: str
    before: int
    after: int
    routes: list[list[str]]  # routes left after the departure


@dataclass
class Rehearsal:
    people: list[str]
    changes: list[Change]  # systems whose number dropped, worst first
    rerun: list[str]  # systems re-run because someone leaving carried them
    total_systems: int
    timings_ms: dict[str, float] = field(default_factory=dict)


def rehearse(db: FalkorDB, graph: str, people: list[str], workers: int = 8) -> Rehearsal:
    t0 = time.perf_counter()
    g = db.select_graph(graph)
    people = sorted(set(people))
    found = {
        r[0]
        for r in g.ro_query(
            "MATCH (p:Person) WHERE p.handle IN $h RETURN p.handle", {"h": people}
        ).result_set
    }
    if missing := [h for h in people if h not in found]:
        raise UnknownPeople(missing)

    # Removing someone who is on no route can't lower a number (those routes stay
    # valid) and can't raise one, so only the systems they CARRY need a new flow.
    rows = g.ro_query(
        """MATCH (p:Person)-[:CARRIES]->(s:System) WHERE p.handle IN $h
           RETURN DISTINCT s.name, s.understudy ORDER BY s.name""",
        {"h": people},
    ).result_set
    before = dict(rows)
    total = g.ro_query("MATCH (s:System) RETURN count(s)").result_set[0][0]

    fork_name = f"rehearsal:{uuid.uuid4().hex[:12]}"
    t1 = time.perf_counter()
    fork = g.copy(fork_name)
    try:
        t2 = time.perf_counter()
        # Deleting a person's ports removes their gate, so no route can pass them.
        fork.query(
            """MATCH (p:Person) WHERE p.handle IN $h SET p.left_at = 'rehearsal'
               WITH collect(p.handle) AS hs
               MATCH (port:Port) WHERE port.person IN hs DETACH DELETE port""",
            {"h": people},
        )
        after: dict[str, Flow] = flows(db, fork_name, list(before), workers)
        t3 = time.perf_counter()
    finally:
        fork.delete()
    t4 = time.perf_counter()

    changes = [
        Change(s, before[s], f.number, f.routes) for s, f in after.items() if f.number != before[s]
    ]
    changes.sort(key=lambda c: (c.after - c.before, c.after, c.system))
    return Rehearsal(
        people=people,
        changes=changes,
        rerun=list(before),
        total_systems=total,
        timings_ms={
            "lookup": round((t1 - t0) * 1000, 1),
            "copy": round((t2 - t1) * 1000, 1),
            "flows": round((t3 - t2) * 1000, 1),
            "cleanup": round((t4 - t3) * 1000, 1),
            "total": round((t4 - t0) * 1000, 1),
        },
    )
