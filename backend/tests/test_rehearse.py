import random

import pytest
from nxflow import assert_valid_routes, nx_number, random_org

from understudy.db import drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.engine.refresh import refresh
from understudy.engine.rehearse import UnknownPeople, rehearse
from understudy.engine.understudy import all_systems, understudy
from understudy.sample import SAMPLE_NUMBERS, SAMPLE_ORG


def load(db, name, org):
    drop(db, name)
    g = db.select_graph(name)
    write_org(g, org)
    build_flow_layer(g)
    refresh(db, name)
    return g


def no_forks_left(db):
    return not [n for n in db.list_graphs() if n.startswith("rehearsal:")]


def test_refresh_writes_numbers_and_carries(db):
    g = load(db, "test:rh-sample", SAMPLE_ORG)
    numbers = dict(g.ro_query("MATCH (s:System) RETURN s.name, s.understudy").result_set)
    assert numbers == SAMPLE_NUMBERS
    carries = g.ro_query(
        "MATCH (p:Person)-[:CARRIES]->(:System {name: 'billing'}) RETURN p.handle"
    ).result_set
    assert "dev" in {r[0] for r in carries} and len(carries) == 2
    # Running it again replaces CARRIES rather than piling more on.
    refresh(db, "test:rh-sample")
    again = g.ro_query("MATCH ()-[c:CARRIES]->() RETURN count(c)").result_set[0][0]
    assert (
        again
        == g.ro_query("MATCH (p)-[:CARRIES]->(s) RETURN count(DISTINCT [id(p), id(s)])").result_set[
            0
        ][0]
    )


def test_dev_steps_back_in_the_sample_org(db):
    g = load(db, "test:rh-sample", SAMPLE_ORG)
    r = rehearse(db, "test:rh-sample", ["dev"])
    assert {c.system: (c.before, c.after) for c in r.changes} == {
        "billing": (1, 0),
        "ledger": (1, 0),
        "auth": (3, 2),
    }
    assert r.changes[0].routes == []  # billing and ledger have no route left
    assert "search" not in r.rerun and "deploys" not in r.rerun
    assert r.total_systems == 5
    assert r.timings_ms["total"] > 0
    assert no_forks_left(db)
    assert {s: understudy(g, s) for s in all_systems(g)} == SAMPLE_NUMBERS


def test_only_carried_systems_are_rerun(db):
    # hal knows billing but can't reach payments, so hal carries nothing anywhere.
    org = {
        **SAMPLE_ORG,
        "people": [*SAMPLE_ORG["people"], {"handle": "hal", "teams": []}],
        "knows": [*SAMPLE_ORG["knows"], {"person": "hal", "system": "billing", "score": 2.0}],
    }
    g = load(db, "test:rh-sample", org)
    carried = dict(
        g.ro_query(
            """MATCH (p:Person) OPTIONAL MATCH (p)-[:CARRIES]->(s:System)
               WITH p, s ORDER BY s.name RETURN p.handle, collect(s.name)"""
        ).result_set
    )
    assert carried["hal"] == []
    for person, systems in carried.items():
        r = rehearse(db, "test:rh-sample", [person])
        assert r.rerun == systems, person
    r = rehearse(db, "test:rh-sample", ["hal"])
    assert r.changes == [] and r.rerun == []


def test_unknown_people_are_refused(db):
    load(db, "test:rh-sample", SAMPLE_ORG)
    with pytest.raises(UnknownPeople) as e:
        rehearse(db, "test:rh-sample", ["dev", "nobody"])
    assert e.value.handles == ["nobody"]
    assert no_forks_left(db)


@pytest.mark.parametrize("seed", range(25))
def test_shortcut_matches_a_full_recompute(db, seed):
    """Re-running only the CARRIES systems must give the same answer as recomputing
    every system from scratch without the people who left."""
    rng = random.Random(1000 + seed)
    org = random_org(rng)
    # The flow layer here uses no score threshold, so neither does the reference.
    name = f"test:rh{seed}"
    load(db, name, org)
    active = [p["handle"] for p in org["people"] if not p.get("left_at")]
    for _ in range(3):
        leaving = set(rng.sample(active, rng.randint(1, min(3, len(active)))))
        r = rehearse(db, name, sorted(leaving))
        changed = {c.system: c for c in r.changes}
        for s in org["systems"]:
            before = nx_number(org, s["name"])
            after = nx_number(org, s["name"], leaving)
            if s["name"] in changed:
                c = changed[s["name"]]
                assert (c.before, c.after) == (before, after), (seed, s["name"], leaving)
                remaining = {
                    **org,
                    "people": [p for p in org["people"] if p["handle"] not in leaving],
                }
                assert_valid_routes(remaining, s["name"], c.routes)
            else:
                assert after == before, (seed, s["name"], leaving)
    assert no_forks_left(db)
    drop(db, name)
