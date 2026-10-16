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


def test_model_counts_what_the_graph_holds(client):
    m = client.get("/api/model").json()
    ports = m["nodes"].pop("Port")
    assert m["nodes"] == {"Person": 7, "System": 5, "Team": 3}
    assert ports == 14  # the flow layer: an in-port and an out-port for each of the 7
    assert m["edges"]["KNOWS"] == len(SAMPLE_ORG["knows"])
    assert m["edges"]["COLLABORATES"] == 2 * len(SAMPLE_ORG["collaborates"])  # both ways
    assert m["edges"]["DEPENDS_ON"] == 6  # auth depends on two teams
    assert m["flow"]["types"] == 5  # one relationship type per system
    assert not any(k.startswith("FLOW_") for k in m["edges"])


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


# --- sessions and fixes -------------------------------------------------------------


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


def test_fixes_proposed_then_applied(client):
    proposal = client.get("/api/fixes/billing").json()
    assert proposal["before"] == 1 and proposal["fixes"]
    best = proposal["fixes"][0]
    assert best["kind"] == "approve" and best["gain"] == 1

    sid = client.post("/api/sessions").json()["id"]
    r = client.post(
        f"/api/sessions/{sid}/fix",
        json={"system": "billing", "kind": best["kind"], "knower": best["knower"]},
    )
    assert r.status_code == 200
    assert {c["system"]: c["after"] for c in r.json()["changes"]}["billing"] == 2
    assert board(client, sid)["billing"] == 2
    assert board(client)["billing"] == 1
    client.delete(f"/api/sessions/{sid}")


def test_fix_requests_are_checked(client):
    sid = client.post("/api/sessions").json()["id"]
    url = f"/api/sessions/{sid}/fix"

    def post(**body):
        return client.post(url, json={"system": "billing", **body})

    assert post(kind="pair", knower="ana").status_code == 422  # no approver
    assert post(kind="approve", knower="esi").status_code == 422  # esi doesn't know billing
    assert post(kind="pair", knower="ana", approver="gus").status_code == 422  # not payments
    assert post(kind="approve", knower="nobody").status_code == 404
    assert post(kind="teach", knower="ana").status_code == 422
    assert (
        client.post(url, json={"system": "nope", "kind": "approve", "knower": "ana"}).status_code
        == 404
    )
    assert client.get("/api/fixes/nope").status_code == 404
    client.delete(f"/api/sessions/{sid}")


def test_start_up_clears_leftover_copies(db):
    drop(db, GRAPH)
    g = db.select_graph(GRAPH)
    write_org(g, SAMPLE_ORG)
    build_flow_layer(g)
    for name in ("rehearsal:old", "scratch:old", "fix:old", "session:old"):
        g.copy(name)
    with TestClient(create_app(GRAPH, db)):
        left = [n for n in db.list_graphs() if n.endswith(":old")]
    assert left == []


# --- the sample org and the built frontend --------------------------------------------


def test_the_sample_org_runs_on_falkordb(db):
    drop(db, "test:sample-api")
    with TestClient(create_app(GRAPH, db, sample="test:sample-api")) as c:
        board = {r["name"]: r["understudy"] for r in c.get("/api/systems?org=sample").json()}
        assert board == SAMPLE_NUMBERS
        assert c.get("/api/systems/billing?org=sample").json()["understudy"] == 1
        r = c.post("/api/rehearse?org=sample", json={"people": ["dev"]}).json()
        assert {x["system"] for x in r["changes"]} == {"billing", "ledger", "auth"}
        assert c.get("/api/systems?org=sample&session=abc").status_code == 422
        assert c.get("/api/systems?org=nope").status_code == 422
    drop(db, "test:sample-api")


def test_the_backtest_is_served_as_written(db, tmp_path):
    proof = tmp_path / "proof.json"
    with TestClient(create_app(GRAPH, db, proof=proof)) as c:
        r = c.get("/api/proof")
        assert r.status_code == 404 and "scripts/backtest.py" in r.json()["detail"]
        proof.write_text('{"summary": {"systems": 3}}')
        r = c.get("/api/proof")
        assert r.json() == {"summary": {"systems": 3}}
        assert r.headers["content-type"].startswith("application/json")


def test_the_built_frontend_is_served(db, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Understudy</title>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    with TestClient(create_app(GRAPH, db, static_dir=tmp_path)) as c:
        assert "Understudy" in c.get("/").text
        assert "Understudy" in c.get("/k8s").text  # client-side routes get index.html
        js = c.get("/assets/app.js")
        assert js.text == "console.log(1)"
        # Browsers refuse a module script with any other type, whatever the OS says.
        assert js.headers["content-type"].startswith("text/javascript")
        assert c.get("/favicon.svg").headers["content-type"].startswith("image/svg+xml")
        assert c.get("/api/nope").status_code == 404  # API paths never fall back
        assert "Understudy" in c.get("/../../etc/passwd").text  # no escaping the folder
