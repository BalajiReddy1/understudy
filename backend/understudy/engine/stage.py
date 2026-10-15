"""Read models for the screens: the call board (every system, riskiest first) and the
stage for one system (who knows it, who approves it, and the routes between them)."""

from falkordb import Graph

from .understudy import flow

BOARD = """
MATCH (s:System)<-[:DEPENDS_ON]-(t:Team)
OPTIONAL MATCH (t)<-[:MEMBER_OF]-(a:Person)
WITH s, t, count(DISTINCT a) AS approvers
OPTIONAL MATCH (s)<-[:KNOWS]-(k:Person)
RETURN s.name, s.understudy, count(DISTINCT k) AS knowers, approvers, t.name, s.sigs,
       s.emeritus_approvers, s.inactive_owners
"""


def call_board(g: Graph) -> list[dict]:
    """Riskiest first: lowest understudy number, then the most people who know the
    system (those are the ones a headcount would wrongly call safe)."""
    rows = [
        {
            "name": name,
            "understudy": understudy,
            "knowers": knowers,
            "approvers": approvers,
            "team": team,
            "sigs": sigs or [],
            "emeritus_approvers": emeritus or 0,
            "inactive_owners": inactive,
        }
        for name, understudy, knowers, approvers, team, sigs, emeritus, inactive in g.ro_query(
            BOARD
        ).result_set
    ]
    return sorted(rows, key=lambda r: (r["understudy"], -r["knowers"], r["name"]))


def stage(g: Graph, system: str) -> dict | None:
    rows = g.ro_query(
        """MATCH (s:System {name: $s})<-[:DEPENDS_ON]-(t:Team)
           RETURN t.name, s.sigs, s.emeritus_approvers, s.inactive_owners""",
        {"s": system},
    ).result_set
    if not rows:
        return None
    team, sigs, emeritus, inactive = rows[0]
    f = flow(g, system)
    on_route = set(f.carriers)

    knowers = g.ro_query(
        """MATCH (p:Person)-[k:KNOWS]->(:System {name: $s})
           RETURN p.handle, k.score ORDER BY k.score DESC, p.handle""",
        {"s": system},
    ).result_set
    approvers = g.ro_query(
        """MATCH (p:Person)-[:MEMBER_OF]->(:Team {name: $t})
           OPTIONAL MATCH (p)-[k:KNOWS]->(:System {name: $s})
           RETURN p.handle, k IS NOT NULL ORDER BY p.handle""",
        {"t": team, "s": system},
    ).result_set
    return {
        "name": system,
        "understudy": f.number,
        "team": team,
        "sigs": sigs or [],
        "emeritus_approvers": emeritus or 0,
        "inactive_owners": inactive,
        "knowers": [
            {"handle": h, "score": score, "on_route": h in on_route} for h, score in knowers
        ],
        "approvers": [
            {"handle": h, "knows": knows, "on_route": h in on_route} for h, knows in approvers
        ],
        "routes": f.routes,
    }
