"""Reference understudy numbers from networkx, built straight from an org dict without
going near FalkorDB, so the engine has something independent to be checked against."""

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
