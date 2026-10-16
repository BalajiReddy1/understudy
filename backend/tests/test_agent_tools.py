import json

import pytest

from understudy.agent import memory
from understudy.agent.tools import BY_NAME, Context, call, resolve_people, resolve_system
from understudy.agent.trace import Recorder, TracedDB
from understudy.db import drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.engine.refresh import refresh
from understudy.engine.sessions import Sessions
from understudy.sample import SAMPLE_ORG

GRAPH = "test:tools"
VISITOR = "test-visitor"
# The tools only take pseudonyms, so the sample org's names are mapped to some.
P = {
    h: f"contributor-{i:04x}"
    for i, h in enumerate(["ana", "ben", "cara", "dev", "esi", "femi", "gus"], 10)
}


def pseudonymised(org: dict) -> dict:
    return {
        **org,
        "people": [{**p, "handle": P[p["handle"]]} for p in org["people"]],
        "knows": [{**k, "person": P[k["person"]]} for k in org["knows"]],
        "collaborates": [[P[a], P[b]] for a, b in org["collaborates"]],
    }


def load(db, name, org):
    drop(db, name)
    g = db.select_graph(name)
    write_org(g, org)
    build_flow_layer(g)
    refresh(db, name)


@pytest.fixture
def ctx(db):
    load(db, GRAPH, pseudonymised(SAMPLE_ORG))
    drop(db, memory.graph_name(VISITOR))
    yield Context(TracedDB(db, Recorder()), GRAPH, VISITOR)
    drop(db, memory.graph_name(VISITOR))
    for n in db.list_graphs():
        if n.startswith("session:"):
            drop(db, n)


def test_resolving_system_names(db):
    names = ["pkg/kubelet", "pkg/kubelet/apis/config", "pkg/kubelet/cm", "pkg/apis/core"]
    org = {
        "teams": ["t"],
        "systems": [{"name": n, "teams": ["t"]} for n in names],
        "people": [],
        "knows": [],
        "collaborates": [],
    }
    load(db, "test:names", org)
    c = Context(db, "test:names")

    def r(text):
        name, problem = resolve_system(c, text)
        return name or (problem.status, problem.data.get("candidates"))

    assert r("pkg/kubelet/cm") == "pkg/kubelet/cm"
    assert r("PKG/Kubelet/CM/") == "pkg/kubelet/cm"
    assert r("kubernetes/pkg/kubelet/cm/cpumanager/state.go") == "pkg/kubelet/cm"
    assert r("kubelet config") == "pkg/kubelet/apis/config"
    assert r("kubelet") == "pkg/kubelet"  # the one whose last part is "kubelet"
    assert r("apis") == ("unknown", ["pkg/apis/core", "pkg/kubelet/apis/config"])
    assert r("nope") == ("unknown", [])
    assert r("test/e2e/node")[0] == "not_in_graph"
    drop(db, "test:names")


def test_resolving_people(ctx):
    assert resolve_people(ctx, [P["dev"], P["dev"].upper()]) == ([P["dev"]], None)
    _, problem = resolve_people(ctx, ["liggitt"])
    assert problem.status == "unknown" and "never looked up" in problem.summary
    _, problem = resolve_people(ctx, ["contributor-ffff"])
    assert problem.status == "unknown" and "contributor-ffff" in problem.summary
    assert resolve_people(ctx, [])[1].status == "unknown"


def test_understudies_says_headcount_and_number(ctx):
    r = call(ctx, "understudies", {"system": "billing"})
    assert r.status == "ok"
    assert r.data["understudy"] == 1 and r.data["knowers"] == 3 and r.data["approvers"] == 1
    assert r.data["critical"] == [P["dev"]]
    assert "3 people know it and 1 can approve" in r.summary and "number is 1" in r.summary
    board = call(ctx, "understudies", {"limit": 2})
    assert [s["name"] for s in board.data["systems"]] == ["billing", "ledger"]


def test_unknown_names_run_nothing(ctx):
    rec = ctx.db.recorder
    rec.take()
    r = call(ctx, "propose_fix", {"system": "payroll"})
    assert r.status == "unknown"
    # Only the name lookup ran: no fork, no fix trials.
    assert [q.cypher for q in rec.take()] == ["MATCH (s:System) RETURN s.name"]


def test_rehearse_and_memory(ctx):
    r = call(ctx, "rehearse_departure", {"people": [P["dev"]]})
    assert r.status == "ok"
    assert {c["system"]: c["after"] for c in r.data["changes"]} == {
        "billing": 0,
        "ledger": 0,
        "auth": 2,
    }
    assert "3 of 5 systems would drop" in r.summary
    copies = [q for q in ctx.db.recorder.take() if q.cypher.startswith("GRAPH.COPY")]
    assert copies and copies[0].graph == "rehearsal:*"

    # What the rehearsal found is remembered, dated, and read back next time.
    again = call(ctx, "understudies", {"system": "billing"})
    assert again.data["remembered"][0]["kind"] == "rehearsal"
    recalled = call(ctx, "recall", {"system": "billing"})
    assert recalled.status == "memory" and "Rehearsal" in recalled.summary


def test_memory_is_per_visitor(ctx, db):
    call(ctx, "rehearse_departure", {"people": [P["dev"]]})
    other = Context(ctx.db, GRAPH, "someone-else")
    assert call(other, "recall", {"system": "billing"}).data["findings"] == []
    assert memory.graph_name("someone-else") not in db.list_graphs()
    with pytest.raises(ValueError):
        memory.graph_name("../../etc")


def test_propose_fix(ctx):
    r = call(ctx, "propose_fix", {"system": "billing"})
    assert r.status == "ok" and r.data["fixes"]
    best = r.data["fixes"][0]
    assert best["kind"] == "approve" and best["gain"] == 1
    assert "from 1 to 2" in r.summary
    nothing = call(ctx, "propose_fix", {"system": "ledger"})
    assert nothing.data["fixes"] == [] and "already on a route" in nothing.summary


def test_actions_need_a_session_and_stay_in_it(ctx, db):
    r = call(ctx, "take_off_stage", {"people": [P["dev"]]})
    assert r.status == "unknown" and "session" in r.summary

    sessions = Sessions(lambda: db, GRAPH)
    state = {}

    def session():
        if "s" not in state:
            state["s"] = sessions.start()
        return state["s"].id, state["s"].graph

    ctx.session = session
    r = call(ctx, "take_off_stage", {"people": [P["dev"]]})
    assert r.status == "ok" and "own copy" in r.summary
    assert ctx.graph == state["s"].graph  # reads now follow the visitor's copy
    assert call(ctx, "understudies", {"system": "billing"}).data["understudy"] == 0
    base = Context(db, GRAPH)
    assert call(base, "understudies", {"system": "billing"}).data["understudy"] == 1

    bad = call(ctx, "apply_fix", {"system": "auth", "kind": "approve", "knower": P["esi"]})
    assert bad.status == "unknown" and "doesn't know" in bad.summary
    fix = call(ctx, "apply_fix", {"system": "search", "kind": "approve", "knower": P["femi"]})
    assert fix.status == "ok" and fix.data["fix"]["team"] == "growth"


def test_who_can_review(ctx):
    r = call(ctx, "who_can_review", {"system": "billing"})
    handles = [x["handle"] for x in r.data["reviewers"]]
    assert set(handles) == {P["ana"], P["ben"], P["cara"]}
    assert all(x["works_with_approvers"] == 1 for x in r.data["reviewers"])
    assert "add a route" in r.summary


def test_the_catalogue_refuses_bad_calls(ctx):
    assert call(ctx, "drop_everything", {}).status == "unknown"
    assert "doesn't take" in call(ctx, "find_system", {"query": "x", "cypher": "MATCH"}).summary
    assert "needs" in call(ctx, "rehearse_departure", {}).summary
    # Every tool's schema is valid JSON with a description, as models and MCP need.
    for tool in BY_NAME.values():
        assert tool.description and json.dumps(tool.parameters)
