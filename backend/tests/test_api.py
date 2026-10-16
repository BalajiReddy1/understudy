import pytest
from fastapi.testclient import TestClient

from understudy.api import create_app
from understudy.db import drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.sample import SAMPLE_NUMBERS, SAMPLE_ORG

GRAPH = "test:api"


@pytest.fixture
def client(db):
    drop(db, GRAPH)
    g = db.select_graph(GRAPH)
    # Numbers and CARRIES are left out on purpose: the app must fill them in on start.
    write_org(
        g, {**SAMPLE_ORG, "systems": [{**s, "sigs": ["sig-x"]} for s in SAMPLE_ORG["systems"]]}
    )
    build_flow_layer(g)
    with TestClient(create_app(GRAPH, db)) as c:
        yield c


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True, "graph": GRAPH, "systems": 5}


def test_call_board_is_riskiest_first(client):
    board = client.get("/api/systems").json()
    assert {r["name"]: r["understudy"] for r in board} == SAMPLE_NUMBERS
    # Billing and ledger both score 1; billing has more knowers, so it comes first.
    assert [r["name"] for r in board][:2] == ["billing", "ledger"]
    billing = board[0]
    assert billing["knowers"] == 3 and billing["approvers"] == 1
    assert billing["team"] == "payments" and billing["sigs"] == ["sig-x"]


def test_stage_matches_the_board(client):
    board = {r["name"]: r for r in client.get("/api/systems").json()}
    for name, row in board.items():
        s = client.get(f"/api/systems/{name}").json()
        assert s["understudy"] == row["understudy"] == len(s["routes"])
        assert len(s["knowers"]) == row["knowers"]
        assert len(s["approvers"]) == row["approvers"]


def test_stage_for_billing(client):
    s = client.get("/api/systems/billing").json()
    assert s["understudy"] == 1
    assert [k["handle"] for k in s["knowers"]] == ["ana", "ben", "cara"]
    assert s["approvers"] == [
        {"handle": "dev", "knows": False, "on_route": True, "critical": True, "off_stage": False}
    ]
    assert len(s["routes"]) == 1 and s["routes"][0][-1] == "dev"
    assert sum(k["on_route"] for k in s["knowers"]) == 1
    # Any of the three knowers could feed dev, so none of them is critical.
    assert not any(k["critical"] for k in s["knowers"])
    assert client.get("/api/systems").json()[0]["critical"] == 1


def test_system_names_with_slashes(db, client):
    g = db.select_graph(GRAPH)
    g.query("MATCH (s:System {name: 'billing'}) SET s.name = 'pkg/billing/v1'")
    assert client.get("/api/systems/pkg/billing/v1").json()["understudy"] == 1


def test_unknown_system(client):
    r = client.get("/api/systems/nope")
    assert r.status_code == 404 and "nope" in r.json()["detail"]


def test_rehearse(client, db):
    r = client.post("/api/rehearse", json={"people": ["dev"]})
    assert r.status_code == 200
    body = r.json()
    assert {c["system"]: (c["before"], c["after"]) for c in body["changes"]} == {
        "billing": (1, 0),
        "ledger": (1, 0),
        "auth": (3, 2),
    }
    assert body["people"] == ["dev"] and body["total_systems"] == 5
    assert set(body["timings_ms"]) == {"lookup", "copy", "flows", "cleanup", "total"}
    # The base graph is untouched and no copy is left behind.
    board = client.get("/api/systems").json()
    assert {x["name"]: x["understudy"] for x in board} == SAMPLE_NUMBERS
    assert not [n for n in db.list_graphs() if n.startswith("rehearsal:")]


def test_rehearse_rejects_bad_requests(client):
    r = client.post("/api/rehearse", json={"people": ["dev", "nobody"]})
    assert r.status_code == 404 and "nobody" in r.json()["detail"]
    assert client.post("/api/rehearse", json={"people": []}).status_code == 422
    too_many = {"people": [f"p{i}" for i in range(11)]}
    assert client.post("/api/rehearse", json=too_many).status_code == 422
    assert client.post("/api/rehearse", json={}).status_code == 422


def test_graph_not_built(db):
    drop(db, "test:api-missing")
    with TestClient(create_app("test:api-missing", db)) as c:
        r = c.get("/api/health")
    assert r.status_code == 503 and "build_org" in r.json()["detail"]


def test_database_down_then_back(db, monkeypatch):
    # Nothing listens on port 1: the app must still start and answer 503.
    monkeypatch.setenv("FALKORDB_PORT", "1")
    app = create_app(GRAPH)
    with TestClient(app) as c:
        r = c.get("/api/systems")
        assert r.status_code == 503 and "30 seconds" in r.json()["detail"]
        # FalkorDB comes up: the next request connects and works.
        monkeypatch.setenv(
            "FALKORDB_PORT", str(db.connection.connection_pool.connection_kwargs["port"])
        )
        drop(db, GRAPH)
        write_org(db.select_graph(GRAPH), SAMPLE_ORG)
        assert c.get("/api/health").json()["systems"] == 5


# --- sessions -------------------------------------------------------------


def board(client, session=None) -> dict:
    url = "/api/systems" + (f"?session={session}" if session else "")
    return {r["name"]: r["understudy"] for r in client.get(url).json()}


def test_a_visitor_session(client, db):
    sid = client.post("/api/sessions").json()["id"]

    r = client.post(f"/api/sessions/{sid}/depart", json={"people": ["dev"]})
    assert r.status_code == 200
    assert {c["system"] for c in r.json()["changes"]} == {"billing", "ledger", "auth"}
    assert board(client, sid)["billing"] == 0
    assert board(client) == SAMPLE_NUMBERS  # the base graph never changes

    stage = client.get(f"/api/systems/billing?session={sid}").json()
    assert stage["approvers"][0]["off_stage"] is True and stage["routes"] == []

    # A rehearsal on top of the session starts from the session's state.
    r = client.post(f"/api/rehearse?session={sid}", json={"people": ["gus"]})
    assert {c["system"]: c["after"] for c in r.json()["changes"]}["auth"] == 1

    log = client.get(f"/api/sessions/{sid}").json()["changes"]
    assert log == [{"type": "depart", "people": ["dev"]}]

    assert client.post(f"/api/sessions/{sid}/reset").json()["changes"] == []
    assert board(client, sid) == SAMPLE_NUMBERS
    assert client.delete(f"/api/sessions/{sid}").json() == {"ok": True}
    r = client.get(f"/api/systems?session={sid}")
    assert r.status_code == 404 and "Start a new one" in r.json()["detail"]
    assert not [n for n in db.list_graphs() if n.startswith("session:")]


def test_start_up_clears_leftover_copies(db):
    drop(db, GRAPH)
    g = db.select_graph(GRAPH)
    write_org(g, SAMPLE_ORG)
    build_flow_layer(g)
    for name in ("rehearsal:old", "scratch:old", "session:old"):
        g.copy(name)
    with TestClient(create_app(GRAPH, db)):
        left = [n for n in db.list_graphs() if n.endswith(":old")]
    assert left == []
