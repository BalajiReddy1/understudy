"""Test helpers: reference understudy numbers from networkx, built straight from an
org dict without going near FalkorDB, plus random orgs to compare them on."""

import random
from itertools import pairwise

import networkx as nx


def nx_number(
    org: dict, system: str, without: set[str] = frozenset(), min_score: float = 0.0
) -> int:
    active = {p["handle"] for p in org["people"] if not p.get("left_at")} - set(without)
    G = nx.DiGraph()
    for h in active:
        G.add_edge(("in", h), ("out", h), capacity=1)
    for k in org["knows"]:
        if k["system"] == system and k["person"] in active and k["score"] >= min_score:
            G.add_edge(("sys", system), ("in", k["person"]), capacity=1)
    for a, b in org["collaborates"]:
        if a in active and b in active and a != b:
            G.add_edge(("out", a), ("in", b), capacity=1)
            G.add_edge(("out", b), ("in", a), capacity=1)
    for p in org["people"]:
        if p["handle"] in active:
            for t in p["teams"]:
                G.add_edge(("out", p["handle"]), ("team", t), capacity=1)
    deps = next(s["teams"] for s in org["systems"] if s["name"] == system)
    for t in deps:
        G.add_edge(("team", t), "SINK")  # no capacity attribute means unbounded
    G.add_node(("sys", system))
    G.add_node("SINK")
    return int(nx.maximum_flow_value(G, ("sys", system), "SINK"))


def assert_valid_routes(org: dict, system: str, routes: list[list[str]], min_score=0.0):
    """Each route starts with someone who knows the system, steps only between
    collaborators, ends with a member of a team that depends on it, and no person is
    on two routes (each person is a gate of capacity 1)."""
    active = {p["handle"] for p in org["people"] if not p.get("left_at")}
    knowers = {
        k["person"]
        for k in org["knows"]
        if k["system"] == system and k["score"] >= min_score and k["person"] in active
    }
    deps = set(next(s["teams"] for s in org["systems"] if s["name"] == system))
    members = {p["handle"] for p in org["people"] if deps & set(p["teams"])} & active
    pairs = {frozenset(c) for c in org["collaborates"]}
    seen = set()
    for route in routes:
        assert route, "empty route"
        assert route[0] in knowers, (system, route)
        assert route[-1] in members, (system, route)
        for a, b in pairwise(route):
            assert frozenset((a, b)) in pairs, (system, route)
        assert not seen & set(route), f"{system}: a person is on two routes"
        seen |= set(route)


def random_org(rng: random.Random) -> dict:
    teams = [f"t{i}" for i in range(rng.randint(2, 5))]
    handles = [f"p{i}" for i in range(rng.randint(6, 25))]
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
