"""Read models for the screens: the call board (every system, riskiest first) and the
stage for one system (who knows it, who approves it, and the routes between them).

People taken off stage in a session keep their KNOWS and MEMBER_OF edges, so the stage
can still show them (marked off stage); counts include only people still on stage."""

from falkordb import Graph

from .understudy import flow

BOARD = """
MATCH (s:System)<-[:DEPENDS_ON]-(t:Team)
OPTIONAL MATCH (t)<-[:MEMBER_OF]-(a:Person) WHERE a.left_at IS NULL
WITH s, collect(DISTINCT t.name) AS teams, count(DISTINCT a) AS approvers
OPTIONAL MATCH (s)<-[:KNOWS]-(k:Person) WHERE k.left_at IS NULL
WITH s, teams, approvers, count(DISTINCT k) AS knowers
OPTIONAL MATCH (s)<-[:CRITICAL]-(c:Person)
WITH s, teams, approvers, knowers, count(DISTINCT c) AS critical
RETURN s.name, s.understudy, knowers, approvers, critical, teams,
       s.sigs, s.emeritus_approvers, s.inactive_owners
"""
# All aggregation finishes in a WITH. On 6.0.2, an aggregating RETURN after an
# aggregating WITH that returns a property null on every row panics the server
# (reply.rs: unwrap on None), e.g. when no system has `sigs` set.


def call_board(g: Graph) -> list[dict]:
    """Riskiest first: lowest understudy number, then the most people who know the
    system (those are the ones a headcount would wrongly call safe)."""
    rows = [
        {
            "name": name,
            "understudy": understudy,
            "knowers": knowers,
            "approvers": approvers,
            "critical": critical,
            "team": ", ".join(sorted(teams)),
            "sigs": sigs or [],
            "emeritus_approvers": emeritus or 0,
            "inactive_owners": inactive,
        }
        for (
            name,
            understudy,
            knowers,
            approvers,
            critical,
            teams,
            sigs,
            emeritus,
            inactive,
        ) in g.ro_query(BOARD).result_set
    ]
    return sorted(rows, key=lambda r: (r["understudy"], -r["knowers"], r["name"]))


def stage(g: Graph, system: str) -> dict | None:
    rows = g.ro_query(
        """MATCH (s:System {name: $s})<-[:DEPENDS_ON]-(t:Team)
           WITH s, collect(t.name) AS teams
           RETURN teams, s.sigs, s.emeritus_approvers, s.inactive_owners""",
        {"s": system},
    ).result_set
    if not rows:
        return None
    teams, sigs, emeritus, inactive = rows[0]
    f = flow(g, system)
    on_route = set(f.carriers)
    critical = {
        r[0]
        for r in g.ro_query(
            "MATCH (p:Person)-[:CRITICAL]->(:System {name: $s}) RETURN p.handle", {"s": system}
        ).result_set
    }

    knowers = g.ro_query(
        """MATCH (p:Person)-[k:KNOWS]->(:System {name: $s})
           RETURN p.handle, k.score, p.left_at IS NOT NULL
           ORDER BY k.score DESC, p.handle""",
        {"s": system},
    ).result_set
    approvers = g.ro_query(
        """MATCH (p:Person)-[:MEMBER_OF]->(:Team)-[:DEPENDS_ON]->(s:System {name: $s})
           WITH DISTINCT p, s
           OPTIONAL MATCH (p)-[k:KNOWS]->(s)
           RETURN p.handle, k IS NOT NULL, p.left_at IS NOT NULL ORDER BY p.handle""",
        {"s": system},
    ).result_set

    def person(h: str, off: bool) -> dict:
        return {"handle": h, "on_route": h in on_route, "critical": h in critical, "off_stage": off}

    return {
        "name": system,
        "understudy": f.number,
        "team": ", ".join(sorted(teams)),
        "sigs": sigs or [],
        "emeritus_approvers": emeritus or 0,
        "inactive_owners": inactive,
        "knowers": [{**person(h, off), "score": score} for h, score, off in knowers],
        "approvers": [{**person(h, off), "knows": knows} for h, knows, off in approvers],
        "routes": f.routes,
    }
