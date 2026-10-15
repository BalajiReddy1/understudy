"""Test helpers: independent reference answers for the understudy number, built
straight from an org dict without going near FalkorDB, plus random orgs to compare.

The definition being checked: a route is an approver who knows the system, or someone
who knows it plus an approver they collaborate with. The number is the most routes
that share no person.
"""

import random
from itertools import pairwise

import networkx as nx


def _sides(org: dict, system: str, without: set[str], min_score: float):
    active = {p["handle"] for p in org["people"] if not p.get("left_at")} - set(without)
    knowers = {
        k["person"]
        for k in org["knows"]
        if k["system"] == system and k["person"] in active and k["score"] >= min_score
    }
    deps = set(next(s["teams"] for s in org["systems"] if s["name"] == system))
    approvers = {p["handle"] for p in org["people"] if deps & set(p["teams"])} & active
    pairs = {frozenset(c) for c in org["collaborates"] if c[0] != c[1]}
    return knowers, approvers, pairs


def nx_number(
    org: dict, system: str, without: set[str] = frozenset(), min_score: float = 0.0
) -> int:
    """Max-flow in networkx on a looser graph than the engine's: channels run into
    every approver, including ones who know the system, so chains like knower ->
    approver -> approver are possible here. Any chain can be cut down to its last
    knower, so the number must still come out the same."""
    knowers, approvers, pairs = _sides(org, system, without, min_score)
    G = nx.DiGraph()
    G.add_nodes_from(["S", "T"])
    for x in knowers | approvers:
        G.add_edge(("in", x), ("out", x), capacity=1)
    for k in knowers:
        G.add_edge("S", ("in", k), capacity=1)
        for a in approvers:
            if a != k and frozenset((k, a)) in pairs:
                G.add_edge(("out", k), ("in", a), capacity=1)
    for a in approvers:
        G.add_edge(("out", a), "T", capacity=1)
    return int(nx.maximum_flow_value(G, "S", "T"))


def brute_number(
    org: dict, system: str, without: set[str] = frozenset(), min_score: float = 0.0
) -> int:
    """The definition taken literally: try every way of giving each knower a route
    (alone if they approve, or paired with a collaborating approver) and keep the
    largest set that shares nobody. Exponential, so only for small orgs."""
    knowers, approvers, pairs = _sides(org, system, without, min_score)
    order = sorted(knowers)

    def best(i: int, used: frozenset) -> int:
        if i == len(order):
            return 0
        k = order[i]
        result = best(i + 1, used)  # k takes no route
        if k not in used:
            if k in approvers:
                result = max(result, 1 + best(i + 1, used | {k}))
            for a in approvers:
                if a != k and a not in used and frozenset((k, a)) in pairs:
                    result = max(result, 1 + best(i + 1, used | {k, a}))
        return result

    return best(0, frozenset())


def assert_valid_routes(org: dict, system: str, routes: list[list[str]], min_score=0.0):
    """Each route is [approver who knows it] or [knower, approver who doesn't know it]
    where the two collaborate, and no person is on two routes."""
    knowers, approvers, pairs = _sides(org, system, set(), min_score)
    seen = set()
    for route in routes:
        assert len(route) in (1, 2), (system, route)
        assert route[0] in knowers, (system, route)
        assert route[-1] in approvers, (system, route)
        if len(route) == 2:
            assert route[1] not in knowers, (system, route)
            for a, b in pairwise(route):
                assert frozenset((a, b)) in pairs, (system, route)
        assert not seen & set(route), f"{system}: a person is on two routes"
        seen |= set(route)


def random_org(rng: random.Random, max_people: int = 25) -> dict:
    teams = [f"t{i}" for i in range(rng.randint(2, 5))]
    handles = [f"p{i}" for i in range(rng.randint(6, max_people))]
    people = [
        {
            "handle": h,
            "teams": rng.sample(teams, rng.choice([0, 1, 1, 1, 2])),
            "left_at": "2025-01-01" if rng.random() < 0.1 else None,
        }
        for h in handles
    ]
    systems = [
        {"name": f"s{i}", "teams": rng.sample(teams, rng.randint(1, 2))}
        for i in range(rng.randint(2, 6))
    ]
    knows = [
        {"person": h, "system": s["name"], "score": round(rng.uniform(0.5, 3), 2)}
        for s in systems
        for h in rng.sample(handles, rng.randint(0, min(6, len(handles))))
    ]
    pairs = {
        tuple(rng.sample(handles, 2)) for _ in range(rng.randint(len(handles), 3 * len(handles)))
    }
    # Half the orgs use the Kubernetes shape (team dicts, extra system properties), so
    # both ways write_org can be called go through the comparison.
    if rng.random() < 0.5:
        teams = [{"name": t, "kind": "approvers"} for t in teams]
        for s in systems:
            s |= {"sigs": ["sig-x"], "emeritus_approvers": 1, "inactive_owners": None}
    return {
        "teams": teams,
        "systems": systems,
        "people": people,
        "knows": knows,
        "collaborates": [list(p) for p in pairs],
    }
