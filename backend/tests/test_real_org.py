"""The whole pipeline on the real Kubernetes pull, checked against networkx. Skipped
when data/raw is missing (CI, or a fresh clone before running scripts/fetch.py)."""

import random
from datetime import date

import pytest
from nxflow import assert_valid_routes, nx_number

from understudy.config import RAW
from understudy.db import drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.derive.kubernetes import derive_org, load_aliases, load_owners, load_prs
from understudy.engine.understudy import all_systems, flow, understudy

AS_OF = date(2026, 10, 14)

pytestmark = pytest.mark.skipif(
    not (RAW / "prs").exists() or not (RAW / "owners" / "history.json").exists(),
    reason="needs the raw Kubernetes pull in data/raw",
)


@pytest.fixture(scope="module")
def org():
    prs = load_prs(RAW)
    org, _ = derive_org(prs, load_owners(RAW, AS_OF), load_aliases(RAW), AS_OF, "test-salt")
    return org


@pytest.fixture
def graph(db, org):
    drop(db, "test:real")
    g = db.select_graph("test:real")
    write_org(g, org)
    build_flow_layer(g)
    return g


def test_org_shape(org):
    assert len(org["systems"]) > 50
    assert all(p["handle"].startswith("contributor-") for p in org["people"])
    assert all(len(s["teams"]) == 1 for s in org["systems"])
    assert all(k["score"] >= 1.0 for k in org["knows"])
    team_names = {t["name"] for t in org["teams"]}
    assert {t for p in org["people"] for t in p["teams"]} <= team_names
    assert {s["teams"][0] for s in org["systems"]} == team_names


def test_graph_matches_the_org(graph, org):
    count = lambda q: graph.ro_query(q).result_set[0][0]
    assert count("MATCH (p:Person) RETURN count(p)") == len(org["people"])
    assert count("MATCH (s:System) RETURN count(s)") == len(org["systems"])
    assert count("MATCH ()-[k:KNOWS]->() RETURN count(k)") == len(org["knows"])
    assert count("MATCH ()-[c:COLLABORATES]->() RETURN count(c)") == 2 * len(org["collaborates"])
    members = sum(len(p["teams"]) for p in org["people"])
    assert count("MATCH ()-[m:MEMBER_OF]->() RETURN count(m)") == members

    # Every kind of flow edge is there in full. On 6.0.2 a property-map MATCH can
    # silently return nothing, which would show up here as a missing kind.
    kinds = dict(graph.ro_query("MATCH ()-[e:KNOWLEDGE]->() RETURN e.kind, count(e)").result_set)
    assert kinds == {
        "gate": len(org["people"]),
        "knows": len(org["knows"]),
        "channel": 2 * len(org["collaborates"]),
        "member": members,
    }


def test_every_system_matches_networkx(graph, org):
    rng = random.Random(7)
    handles = [p["handle"] for p in org["people"]]
    for s in all_systems(graph):
        f = flow(graph, s)
        assert f.number == nx_number(org, s), s
        assert len(f.routes) == f.number
        assert_valid_routes(org, s, f.routes)
        # Losing someone who carries none of the routes can't change the number.
        for h in rng.sample(handles, 3):
            if h not in f.carriers:
                assert nx_number(org, s, {h}) == f.number, (s, h)


def test_rehearsal_on_a_copy(db, graph, org):
    target = next(s for s in all_systems(graph) if understudy(graph, s) >= 2)
    before = understudy(graph, target)
    leaving = flow(graph, target).carriers[0]

    drop(db, "test:real-fork")
    fork = graph.copy("test:real-fork")
    fork.query(
        """MATCH (p:Person {handle: $h}) SET p.left_at = '2026-10-15'
           WITH p.handle AS h MATCH (port:Port) WHERE port.person = h DETACH DELETE port""",
        {"h": leaving},
    )
    after = understudy(fork, target)
    assert after == nx_number(org, target, {leaving})
    assert before - 1 <= after <= before
    assert understudy(graph, target) == before  # the original is untouched
    drop(db, "test:real-fork")
