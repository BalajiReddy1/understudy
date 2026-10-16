"""Understudy as an MCP server, so Claude Code, Cursor or any MCP client can use the
same tools as the in-app agent.

    uv run python -m understudy.mcp_server            # stdio, for Claude Code
    uv run python -m understudy.mcp_server --http     # read-only tools over HTTP

Every tool returns {status, summary, data, queries}: the answer type, a plain
sentence, the facts, and the Cypher that ran with its timing. Tools that change
things only ever touch this server's own session copy of the graph, and aren't
offered over HTTP at all.
"""

import argparse
import os
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from .agent.tools import Context, call
from .agent.trace import Recorder, TracedDB
from .db import connect
from .engine.sessions import Sessions

SystemArg = Annotated[str, Field(description="A system name, a path inside one, or words for it")]
PeopleArg = Annotated[
    list[str], Field(description="Pseudonyms like contributor-1a2b", max_length=10)
]


class QueryRun(BaseModel):
    graph: str
    cypher: str
    ms: float
    rows: int


class Answer(BaseModel):
    status: Literal["ok", "memory", "not_in_graph", "unknown"] = Field(
        description="ok: answered from the graph. memory: from an earlier session's "
        "findings. not_in_graph: nothing modelled covers it, which is not the same as "
        "safe. unknown: a name didn't resolve, so nothing ran."
    )
    summary: str = Field(description="One plain sentence built from the data")
    data: dict[str, Any]
    queries: list[QueryRun] = Field(description="The Cypher that ran, with timings")


_state: dict = {}


def _ctx() -> Context:
    """One context per server process, made on first use so the server starts even
    while FalkorDB is still coming up."""
    if "ctx" not in _state:
        db = connect()
        base = os.environ.get("UNDERSTUDY_GRAPH", "org")
        sessions = Sessions(lambda: db, base)
        holder: dict = {}

        def session() -> tuple[str, str]:
            if "s" not in holder:
                holder["s"] = sessions.start()
            return holder["s"].id, holder["s"].graph

        recorder = Recorder()
        _state["recorder"] = recorder
        _state["ctx"] = Context(
            TracedDB(db, recorder),
            base,
            visitor=os.environ.get("UNDERSTUDY_VISITOR", "mcp"),
            session=session,
        )
    return _state["ctx"]


def _run(name: str, **args) -> Answer:
    ctx = _ctx()
    _state["recorder"].take()
    r = call(ctx, name, {k: v for k, v in args.items() if v is not None})
    queries = [
        QueryRun(graph=q.graph, cypher=q.cypher[:400], ms=q.ms, rows=q.rows)
        for q in _state["recorder"].take()
    ]
    return Answer(status=r.status, summary=r.summary, data=r.data, queries=queries)


def find_system(query: Annotated[str, Field(description="What the user called it")]) -> Answer:
    """Resolve what someone called a system to its exact name, or list candidates."""
    return _run("find_system", query=query)


def understudies(
    system: Annotated[str | None, Field(description="Leave out for the riskiest systems")] = None,
    limit: Annotated[int, Field(ge=1, le=20)] = 5,
) -> Answer:
    """How exposed a system is: how many people know it, how many approve it, and its
    understudy number (how many people can leave before no approver can reach anyone
    who knows it), with its routes and critical people."""
    return _run("understudies", system=system, limit=limit)


def rehearse_departure(people: PeopleArg) -> Answer:
    """What breaks if these people leave, rehearsed on a copy of the graph."""
    return _run("rehearse_departure", people=people)


def propose_fix(system: SystemArg) -> Answer:
    """The cheapest changes that raise a system's number, each tried on its own copy."""
    return _run("propose_fix", system=system)


def who_can_review(system: SystemArg) -> Answer:
    """Who knows a system best, and whether they approve it or are critical to it."""
    return _run("who_can_review", system=system)


def why(system: SystemArg) -> Answer:
    """The KEPs behind a system and whether their approvers are still active."""
    return _run("why", system=system)


def recall(system: SystemArg) -> Answer:
    """What earlier sessions found about a system, with dates."""
    return _run("recall", system=system)


def take_off_stage(people: PeopleArg) -> Answer:
    """Take people off stage in this server's own copy of the company."""
    return _run("take_off_stage", people=people)


def apply_fix(
    system: SystemArg,
    kind: Literal["pair", "approve"],
    knower: Annotated[str, Field(description="Someone who knows the system")],
    approver: Annotated[str | None, Field(description="For 'pair': an approver")] = None,
) -> Answer:
    """Apply a fix in this server's own copy: pair a knower with an approver, or
    make the knower an approver."""
    return _run("apply_fix", system=system, kind=kind, knower=knower, approver=approver)


READ_ONLY = [
    find_system,
    understudies,
    rehearse_departure,
    propose_fix,
    who_can_review,
    why,
    recall,
]
ACTIONS = [take_off_stage, apply_fix]


def build(read_only: bool = False):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        "understudy",
        instructions=(
            "Understudy measures how many people can leave before nobody who approves a "
            "Kubernetes system can reach anyone who knows it. People appear only as "
            "pseudonyms. Resolve names with find_system when unsure. Cite the routes and "
            "numbers the tools return; say so when a result is 'not_in_graph' or 'unknown'."
        ),
    )
    for fn in READ_ONLY + ([] if read_only else ACTIONS):
        server.add_tool(fn, structured_output=True)
    return server


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve the read-only tools over HTTP")
    args = ap.parse_args()
    build(read_only=args.http).run("streamable-http" if args.http else "stdio")


if __name__ == "__main__":
    main()
