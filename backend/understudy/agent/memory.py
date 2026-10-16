"""Agent memory: findings written back as nodes, one graph per visitor.

    (:Finding {text, kind, at, session})-[:ABOUT]->(:System {name})

Each visitor's memory is its own graph, memory:<visitor>, so one visitor's findings
can't reach another's: isolation is a separate graph, not a filter someone could
forget. Findings come from what the tools did (rehearsals, fixes), written as plain
sentences built from their results, never from the model's own words."""

import re
from datetime import UTC, datetime

from falkordb import FalkorDB
from redis.exceptions import ResponseError

VISITOR = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")


def graph_name(visitor: str) -> str:
    if not VISITOR.match(visitor):
        raise ValueError("visitor ids are 1 to 40 letters, digits, '-' or '_'")
    return f"memory:{visitor}"


def remember(
    db: FalkorDB, visitor: str, systems: list[str], text: str, kind: str, session: str | None
) -> None:
    g = db.select_graph(graph_name(visitor))
    try:
        g.query("CREATE INDEX FOR (s:System) ON (s.name)")
    except ResponseError:
        pass  # already there
    g.query(
        """CREATE (f:Finding {text: $text, kind: $kind, at: $at, session: $session})
           WITH f UNWIND $systems AS name
           MERGE (s:System {name: name})
           CREATE (f)-[:ABOUT]->(s)""",
        {
            "text": text,
            "kind": kind,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "session": session or "",
            "systems": sorted(set(systems)),
        },
    )


def recall(db: FalkorDB, visitor: str, system: str, limit: int = 5) -> list[dict]:
    name = graph_name(visitor)
    if name not in db.list_graphs():
        return []
    rows = (
        db.select_graph(name)
        .ro_query(
            """MATCH (f:Finding)-[:ABOUT]->(:System {name: $s})
           RETURN f.text, f.kind, f.at ORDER BY f.at DESC LIMIT $n""",
            {"s": system, "n": limit},
        )
        .result_set
    )
    return [{"text": t, "kind": k, "at": at} for t, k, at in rows]
