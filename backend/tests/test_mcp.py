import os
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_agent_tools import P, load, pseudonymised

from understudy.agent import memory
from understudy.db import drop
from understudy.mcp_server import build
from understudy.sample import SAMPLE_ORG

GRAPH = "test:mcp"


def test_tool_lists():
    async def names(read_only: bool) -> set[str]:
        return {t.name for t in await build(read_only).list_tools()}

    everything = anyio.run(names, False)
    assert everything == {
        "find_system",
        "understudies",
        "rehearse_departure",
        "propose_fix",
        "who_can_review",
        "why",
        "recall",
        "take_off_stage",
        "apply_fix",
    }
    assert anyio.run(names, True) == everything - {"take_off_stage", "apply_fix"}


@pytest.fixture
def graph(db):
    load(db, GRAPH, pseudonymised(SAMPLE_ORG))
    yield db
    drop(db, GRAPH)
    drop(db, memory.graph_name("test-mcp"))
    for n in db.list_graphs():
        if n.startswith("session:"):
            drop(db, n)


def test_over_stdio(graph):
    db = graph
    port = db.connection.connection_pool.connection_kwargs["port"]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "understudy.mcp_server"],
        env={
            **os.environ,
            "FALKORDB_PORT": str(port),
            "UNDERSTUDY_GRAPH": GRAPH,
            "UNDERSTUDY_VISITOR": "test-mcp",
        },
    )

    async def session_run():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            billing = (await s.call_tool("understudies", {"system": "billing"})).structured_content
            gone = (await s.call_tool("take_off_stage", {"people": [P["dev"]]})).structured_content
            after = (await s.call_tool("understudies", {"system": "billing"})).structured_content
            unknown = (await s.call_tool("why", {"system": "payroll"})).structured_content
            return tools, billing, gone, after, unknown

    tools, billing, gone, after, unknown = anyio.run(session_run)
    assert len(tools) == 9
    assert billing["status"] == "ok" and billing["data"]["understudy"] == 1
    assert any("algo.maxFlow" in q["cypher"] for q in billing["queries"])
    assert gone["status"] == "ok" and after["data"]["understudy"] == 0
    assert unknown["status"] == "unknown"
    # The departure happened in the server's own session copy, not the base graph.
    base = (
        db.select_graph(GRAPH)
        .ro_query("MATCH (s:System {name: 'billing'}) RETURN s.understudy")
        .result_set[0][0]
    )
    assert base == 1
