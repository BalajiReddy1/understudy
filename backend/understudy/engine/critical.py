"""Critical people: those whose departure alone would lower a system's number.

Only someone on a route can matter (everyone else can leave without changing a
number), but not everyone on a route is critical: if another person could take their
place, losing them costs nothing. So each person on a route is tested directly: their
gate for that system is removed on a scratch copy, max-flow runs again, and the gate
goes back. The checks are split across several scratch copies that run in parallel.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

from falkordb import FalkorDB

from ..db import copy
from .understudy import Flow, flow

# Type names can't be parameters; {T} is always a FLOW_<n> read from the graph.
CLOSE_GATE = """MATCH (i:Port {side: 'in'})-[g]->(:Port {side: 'out'})
                WHERE i.person = $h AND type(g) = $t DELETE g"""
OPEN_GATE = """MATCH (i:Port {side: 'in'}) WHERE i.person = $h
               MATCH (o:Port {side: 'out'}) WHERE o.person = $h
               MERGE (i)-[g:{T} {kind: 'gate'}]->(o) SET g.cap = 1"""
# A copy costs about as much as ten checks, so small jobs don't get split.
CHECKS_PER_COPY = 10


def critical_people(
    db: FalkorDB, graph: str, results: dict[str, Flow], workers: int = 8
) -> dict[str, list[str]]:
    """system -> the people whose departure alone would lower its number."""
    g = db.select_graph(graph)
    types = dict(g.ro_query("MATCH (s:System) RETURN s.name, s.flow").result_set)
    checks = [(s, p) for s, f in results.items() for p in f.carriers]
    out: dict[str, list[str]] = {s: [] for s in results}
    if not checks:
        return out

    def run(chunk: list[tuple[str, str]]) -> list[tuple[str, str]]:
        fork = copy(g, f"scratch:{uuid.uuid4().hex[:12]}")
        found = []
        try:
            for system, person in chunk:
                fork.query(CLOSE_GATE, {"h": person, "t": types[system]})
                without = flow(fork, system)
                fork.query(OPEN_GATE.replace("{T}", types[system]), {"h": person})
                if without.number < results[system].number:
                    found.append((system, person))
        finally:
            fork.delete()
        return found

    n = max(1, min(workers, len(checks) // CHECKS_PER_COPY))
    with ThreadPoolExecutor(n) as pool:
        for found in pool.map(run, [checks[i::n] for i in range(n)]):
            for system, person in found:
                out[system].append(person)
    return {s: sorted(ps) for s, ps in out.items()}
