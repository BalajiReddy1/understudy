"""Fixes: the cheapest change that raises a system's understudy number.

Two kinds of change can add a route, both using someone who knows the system but is
on no route yet (a free knower):
  pair     introduce them to an approver who is on no route, so they can work together
  approve  add them to the system's OWNERS approvers, so they are a route on their own

Each candidate is tried on its own copy of the graph: the change is applied, the flow
edges of every system it touches are rebuilt, and those systems' numbers are compared
with the originals. The target always gains 1 when both people are free; candidates
differ in what else they help, so that decides the ranking, with pairs ahead of OWNERS
changes when they help equally (pairing is cheaper than a new approver).
"""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from falkordb import FalkorDB, Graph

from ..db import copy
from ..derive.flow import build_flow_layer
from .refresh import refresh
from .rehearse import Change
from .understudy import flow, flows

MAX_KNOWERS = 3  # free knowers tried, strongest knowledge first
MAX_TRIES = 8


@dataclass
class Fix:
    kind: str  # "pair" or "approve"
    knower: str
    approver: str | None  # the approver they'd pair with, for "pair"
    team: str  # the OWNERS file (team) the system depends on
    gain: int  # on the system asked about
    also: list[Change] = field(default_factory=list)  # other systems that rise too
    summary: str = ""
    owners_diff: str | None = None  # for "approve": the OWNERS change, as text


@dataclass
class Proposal:
    system: str
    before: int
    fixes: list[Fix]  # best first; empty when no single change can help
    reason: str | None  # why nothing can help, when fixes is empty
    tried: int
    timings_ms: dict[str, float] = field(default_factory=dict)


class UnknownSystem(KeyError):
    pass


class FixRejected(ValueError):
    """A requested fix that can't be applied, with the status an API should answer."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message, self.status = message, status


def checked_fix(g: Graph, system: str, kind: str, knower: str, approver: str | None) -> Fix:
    """Turn a request into a Fix, refusing anything that couldn't do what it claims:
    the knower must know the system, and a pairing's approver must approve it."""
    rows = g.ro_query(
        "MATCH (s:System {name: $s})<-[:DEPENDS_ON]-(t:Team) RETURN t.name", {"s": system}
    ).result_set
    if not rows:
        raise FixRejected(f"No system called '{system}'.", 404)
    if kind not in ("pair", "approve"):
        raise FixRejected("A fix is either 'pair' or 'approve'.", 422)
    if kind == "pair" and not approver:
        raise FixRejected("A pairing needs an approver.", 422)
    people = [knower] + ([approver] if kind == "pair" else [])
    found = {
        r[0]
        for r in g.ro_query(
            "MATCH (p:Person) WHERE p.handle IN $h AND p.left_at IS NULL RETURN p.handle",
            {"h": people},
        ).result_set
    }
    if missing := [h for h in people if h not in found]:
        raise FixRejected(f"Nobody on stage is called {', '.join(missing)}.", 404)
    team = rows[0][0]
    knows = g.ro_query(
        "MATCH (:Person {handle: $k})-[r:KNOWS]->(:System {name: $s}) RETURN count(r)",
        {"k": knower, "s": system},
    ).result_set[0][0]
    if not knows:
        raise FixRejected(f"{knower} doesn't know {system}.", 422)
    if kind == "pair":
        approves = g.ro_query(
            "MATCH (:Person {handle: $a})-[r:MEMBER_OF]->(:Team {name: $t}) RETURN count(r)",
            {"a": approver, "t": team},
        ).result_set[0][0]
        if not approves:
            raise FixRejected(f"{approver} doesn't approve {system}.", 422)
    return Fix(kind, knower, approver if kind == "pair" else None, team, 0)


def _touched(g: Graph, fix: Fix) -> list[str]:
    """Systems whose flow edges the change can alter."""
    if fix.kind == "approve":
        q = "MATCH (:Team {name: $t})-[:DEPENDS_ON]->(s:System) RETURN s.name"
        return [r[0] for r in g.ro_query(q, {"t": fix.team}).result_set]
    # A collaboration opens a channel wherever one of the two knows the system and the
    # other approves it.
    q = """MATCH (x:Person)-[:KNOWS]->(s:System)<-[:DEPENDS_ON]-(:Team)<-[:MEMBER_OF]-(y:Person)
           WHERE (x.handle = $a AND y.handle = $b) OR (x.handle = $b AND y.handle = $a)
           RETURN DISTINCT s.name"""
    return [r[0] for r in g.ro_query(q, {"a": fix.knower, "b": fix.approver}).result_set]


def _apply(g: Graph, fix: Fix) -> None:
    if fix.kind == "approve":
        g.query(
            """MATCH (p:Person {handle: $k}), (t:Team {name: $t})
               MERGE (p)-[:MEMBER_OF]->(t)""",
            {"k": fix.knower, "t": fix.team},
        )
    else:
        g.query(
            """MATCH (a:Person {handle: $a}), (b:Person {handle: $b})
               MERGE (a)-[:COLLABORATES]->(b) MERGE (b)-[:COLLABORATES]->(a)""",
            {"a": fix.knower, "b": fix.approver},
        )


def _describe(fix: Fix, system: str) -> None:
    if fix.kind == "pair":
        fix.summary = (
            f"Pair {fix.knower}, who knows {system}, with {fix.approver}, who approves it, "
            "on reviews there."
        )
    else:
        fix.summary = f"Add {fix.knower}, who knows {system}, to the approvers in {fix.team}."
        fix.owners_diff = f"--- {fix.team}\n+++ {fix.team}\n approvers:\n+  - {fix.knower}\n"


def _try(db: FalkorDB, graph: str, fix: Fix, before: dict[str, int], system: str) -> Fix:
    g = db.select_graph(graph)
    touched = _touched(g, fix)
    name = f"fix:{uuid.uuid4().hex[:12]}"
    fork = copy(g, name)
    try:
        _apply(fork, fix)
        build_flow_layer(fork, systems=touched)
        after = flows(db, name, touched, workers=4)
    finally:
        fork.delete()
    fix.gain = after[system].number - before[system]
    fix.also = sorted(
        (
            Change(s, before[s], f.number, f.routes)
            for s, f in after.items()
            if s != system and f.number != before[s]
        ),
        key=lambda c: (c.before - c.after, c.system),
    )
    return fix


def propose(db: FalkorDB, graph: str, system: str, workers: int = 8) -> Proposal:
    t0 = time.perf_counter()
    g = db.select_graph(graph)
    rows = g.ro_query(
        "MATCH (s:System {name: $s})<-[:DEPENDS_ON]-(t:Team) RETURN t.name", {"s": system}
    ).result_set
    if not rows:
        raise UnknownSystem(system)
    team = rows[0][0]
    current = flow(g, system)
    on_route = set(current.carriers)

    knowers = [
        h
        for h, _ in g.ro_query(
            """MATCH (p:Person)-[k:KNOWS]->(:System {name: $s}) WHERE p.left_at IS NULL
               RETURN p.handle, k.score ORDER BY k.score DESC, p.handle""",
            {"s": system},
        ).result_set
    ]
    approvers = [
        r[0]
        for r in g.ro_query(
            """MATCH (p:Person)-[:MEMBER_OF]->(:Team)-[:DEPENDS_ON]->(:System {name: $s})
               WHERE p.left_at IS NULL RETURN DISTINCT p.handle ORDER BY p.handle""",
            {"s": system},
        ).result_set
    ]
    free_knowers = [k for k in knowers if k not in on_route][:MAX_KNOWERS]
    # An approver on no route can't know the system (they'd be a route on their own),
    # and can't already work with a free knower (that would be a route too).
    free_approvers = [a for a in approvers if a not in on_route]

    # Every "approve" candidate is tried; pairs fill the rest of the budget.
    approves = [Fix("approve", k, None, team, 0) for k in free_knowers]
    pairs = [Fix("pair", k, a, team, 0) for k in free_knowers for a in free_approvers]
    candidates = pairs[: MAX_TRIES - len(approves)] + approves

    reason = None
    if not knowers:
        reason = f"Nobody active knows {system}. Someone has to learn it first."
    elif not free_knowers:
        reason = (
            f"Everyone who knows {system} is already on a route. "
            "Someone new has to learn it before the number can go up."
        )

    stored = dict(g.ro_query("MATCH (s:System) RETURN s.name, s.understudy").result_set)
    before = {s: n if n is not None else 0 for s, n in stored.items()}
    before[system] = current.number
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(candidates)))) as pool:
        tried = list(pool.map(lambda c: _try(db, graph, c, before, system), candidates))

    helpful = [f for f in tried if f.gain > 0]
    for f in helpful:
        _describe(f, system)
    helpful.sort(
        key=lambda f: (
            -f.gain,
            -sum(c.after - c.before for c in f.also),
            f.kind != "pair",
            knowers.index(f.knower),
            f.approver or "",
        )
    )
    return Proposal(
        system=system,
        before=current.number,
        fixes=helpful,
        reason=reason,
        tried=len(tried),
        timings_ms={"total": round((time.perf_counter() - t0) * 1000, 1)},
    )


def apply_fix(db: FalkorDB, graph: str, fix: Fix) -> list[Change]:
    """Make the change for real on `graph` (a session copy) and refresh what it touched."""
    g = db.select_graph(graph)
    touched = _touched(g, fix)
    before = dict(
        g.ro_query(
            "MATCH (s:System) WHERE s.name IN $s RETURN s.name, s.understudy", {"s": touched}
        ).result_set
    )
    _apply(g, fix)
    build_flow_layer(g, systems=touched)
    after = refresh(db, graph, touched)
    return sorted(
        (
            Change(s, before[s], f.number, f.routes)
            for s, f in after.items()
            if f.number != before[s]
        ),
        key=lambda c: (c.before - c.after, c.system),
    )
