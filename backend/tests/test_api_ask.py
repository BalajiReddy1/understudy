import json

import pytest
from fastapi.testclient import TestClient
from test_agent_loop import Script, message, tool_call
from test_agent_tools import P, load, pseudonymised

from understudy.agent import loop, memory
from understudy.api import create_app
from understudy.db import drop
from understudy.sample import SAMPLE_ORG

GRAPH = "test:api-ask"


@pytest.fixture
def base(db):
    load(db, GRAPH, pseudonymised(SAMPLE_ORG))
    yield db
    drop(db, GRAPH)
    drop(db, memory.graph_name("visitor-1"))
    for n in db.list_graphs():
        if n.startswith("session:"):
            drop(db, n)


def app_with(db, responses, limit=(10, 600)):
    return TestClient(create_app(GRAPH, db, ask_client=Script(responses), ask_limit=limit))


def test_ask_returns_the_answer_and_its_trace(base):
    with app_with(
        base,
        [
            message(calls=[tool_call(1, "understudies", {"system": "billing"})]),
            message("billing has 3 people who know it and 1 approver; its number is 1."),
        ],
    ) as c:
        r = c.post("/api/ask", json={"question": "how exposed is billing?"}).json()
    assert r["checked"] and r["kind"] == "path" and r["session"] is None
    step = r["steps"][0]
    assert step["tool"] == "understudies" and step["status"] == "ok"
    assert any("algo.maxFlow" in q["cypher"] for q in step["queries"])


def test_an_agent_change_starts_a_session(base):
    with app_with(
        base,
        [
            message(calls=[tool_call(1, "take_off_stage", {"people": [P["dev"]]})]),
            message("Done: billing dropped from 1 to 0 in your copy."),
        ],
    ) as c:
        r = c.post("/api/ask", json={"question": "take dev off stage"}).json()
        sid = r["session"]
        assert sid and r["checked"]
        board = {x["name"]: x["understudy"] for x in c.get(f"/api/systems?session={sid}").json()}
        assert board["billing"] == 0
        assert {x["name"]: x["understudy"] for x in c.get("/api/systems").json()}["billing"] == 1
        log = c.get(f"/api/sessions/{sid}").json()["changes"]
        assert log == [{"type": "agent", "tool": "take_off_stage", "people": [P["dev"]]}]


def test_memory_belongs_to_the_visitor(base):
    script = [
        message(calls=[tool_call(1, "rehearse_departure", {"people": [P["dev"]]})]),
        message("If they left, 3 of 5 systems would drop."),
    ]
    with app_with(base, script) as c:
        c.post("/api/ask?visitor=visitor-1", json={"question": f"what if {P['dev']} left?"})
        bad = c.post("/api/ask?visitor=../x", json={"question": "hi"})
    assert bad.status_code == 422
    found = memory.recall(base, "visitor-1", "billing")
    assert found and found[0]["kind"] == "rehearsal"


def test_the_question_limit(base):
    answers = [message(calls=[tool_call(i, "understudies", {})]) for i in range(2)]
    script = [answers[0], message("Billing is riskiest."), answers[1], message("Billing again.")]
    with app_with(base, script, limit=(2, 600)) as c:
        codes = [c.post("/api/ask", json={"question": "riskiest?"}).status_code for _ in range(3)]
        board = c.get("/api/systems").status_code
    assert codes == [200, 200, 429] and board == 200  # everything else still works


def test_the_stream(base):
    with app_with(
        base,
        [
            message(calls=[tool_call(1, "understudies", {"system": "billing"})]),
            message("billing's number is 1."),
        ],
    ) as c, c.stream("POST", "/api/ask/stream", json={"question": "billing?"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["tool", "answer"]
    assert events[1]["checked"] and events[0]["tool"] == "understudies"


def test_no_key_still_answers(base, monkeypatch):
    monkeypatch.setattr(loop, "_client", lambda: None)
    with TestClient(create_app(GRAPH, base)) as c:
        r = c.post("/api/ask", json={"question": "why is billing so low?"}).json()
    assert r["model"] is None and r["note"] == "no model key"
    assert [s["tool"] for s in r["steps"]] == ["understudies", "why"]
    assert r["text"].startswith("billing: 3 people know it")


def test_bad_questions_are_refused(base):
    with app_with(base, []) as c:
        assert c.post("/api/ask", json={"question": ""}).status_code == 422
        assert c.post("/api/ask", json={"question": "x" * 501}).status_code == 422
        r = c.post("/api/ask?session=nope", json={"question": "hi"})
        assert r.status_code == 404
