import re

from fastapi.testclient import TestClient

from understudy import mcp_server, snapshot
from understudy.api import create_app
from understudy.config import SNAPSHOT
from understudy.db import drop
from understudy.sample import SAMPLE_NUMBERS, SAMPLE_ORG

GRAPH = "test:snapshot"
PSEUDONYM = re.compile(r"contributor-[0-9a-f]{4,16}")


def test_saving_is_repeatable_and_round_trips(tmp_path):
    a, b = tmp_path / "a.json.gz", tmp_path / "b" / "b.json.gz"
    snapshot.save(SAMPLE_ORG, a)
    snapshot.save(SAMPLE_ORG, b)  # makes the folder
    assert a.read_bytes() == b.read_bytes()  # same org, same bytes, so git sees no change
    assert snapshot.read(a) == SAMPLE_ORG


def test_loading_works_out_every_number(db, tmp_path):
    path = tmp_path / "org.json.gz"
    snapshot.save(SAMPLE_ORG, path)
    snapshot.load(db, GRAPH, path)
    g = db.select_graph(GRAPH)
    assert dict(g.ro_query("MATCH (s:System) RETURN s.name, s.understudy").result_set) == (
        SAMPLE_NUMBERS
    )
    assert g.ro_query("MATCH ()-[c:CARRIES]->() RETURN count(c)").result_set[0][0] > 0


def test_the_app_loads_a_missing_graph_from_the_snapshot(db, tmp_path):
    path = tmp_path / "org.json.gz"
    snapshot.save(SAMPLE_ORG, path)
    drop(db, GRAPH)
    with TestClient(create_app(GRAPH, db, snapshot=path)) as c:
        assert c.get("/api/health").json()["systems"] == 5
        board = {r["name"]: r["understudy"] for r in c.get("/api/systems").json()}
        assert board == SAMPLE_NUMBERS
        # If FalkorDB loses it (a restart with nothing on disk), the next request
        # loads it again instead of failing.
        drop(db, GRAPH)
        assert c.get("/api/systems/billing").json()["understudy"] == 1


def test_without_a_graph_or_a_snapshot_the_app_says_what_to_run(db):
    drop(db, GRAPH)
    with TestClient(create_app(GRAPH, db)) as c:
        r = c.get("/api/health")
        assert r.status_code == 503 and "scripts/build_org.py" in r.json()["detail"]
    assert GRAPH not in db.list_graphs()


def test_the_mcp_server_loads_a_missing_graph_too(db, tmp_path, monkeypatch):
    path = tmp_path / "org.json.gz"
    snapshot.save(SAMPLE_ORG, path)
    drop(db, GRAPH)
    port = db.connection.connection_pool.connection_kwargs["port"]
    monkeypatch.setenv("FALKORDB_PORT", str(port))
    monkeypatch.setenv("UNDERSTUDY_GRAPH", GRAPH)
    monkeypatch.setattr(mcp_server, "SNAPSHOT", path)
    monkeypatch.setattr(mcp_server, "_state", {})
    ctx = mcp_server._ctx()
    assert ctx.graph == GRAPH
    numbers = db.select_graph(GRAPH).ro_query("MATCH (s:System) RETURN s.name, s.understudy")
    assert dict(numbers.result_set) == SAMPLE_NUMBERS


def test_the_committed_snapshot_holds_pseudonyms_only():
    org = snapshot.read(SNAPSHOT)
    handles = [p["handle"] for p in org["people"]]
    handles += [k["person"] for k in org["knows"]]
    handles += [h for pair in org["collaborates"] for h in pair]
    handles += [a for d in org.get("decisions", []) for a in d["approvers"]]
    assert handles and all(PSEUDONYM.fullmatch(h) for h in handles)
