import copy
import random

import pytest
from nxflow import nx_number, random_org
from test_critical import critical_in_graph, expected_critical

from understudy.db import drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.engine.fixes import apply_fix, propose
from understudy.engine.refresh import refresh
from understudy.sample import SAMPLE_ORG


def load(db, name, org):
    drop(db, name)
    g = db.select_graph(name)
    write_org(g, org)
    build_flow_layer(g)
    refresh(db, name)
    return g


def changed(org: dict, fix) -> dict:
    """The org with the fix made, for the networkx reference."""
    org = copy.deepcopy(org)
    if fix.kind == "pair":
        org["collaborates"].append([fix.knower, fix.approver])
    else:
        for p in org["people"]:
            if p["handle"] == fix.knower:
                p["teams"] = [*p["teams"], fix.team]
    return org


def team_name(t) -> str:
    return t["name"] if isinstance(t, dict) else t


def test_billing_in_the_sample_org(db):
    load(db, "test:fix-sample", SAMPLE_ORG)
    p = propose(db, "test:fix-sample", "billing")
    assert p.before == 1 and p.reason is None
    # dev is the only payments approver and already on the route, so nobody is free to
    # pair with: the fixes are making one of the two idle knowers an approver.
    assert {f.kind for f in p.fixes} == {"approve"}
    assert all(f.gain == 1 for f in p.fixes)
    best = p.fixes[0]
    assert best.knower in {"ana", "ben", "cara"} and best.team == "payments"
    assert f"+  - {best.knower}" in best.owners_diff
    assert not [n for n in db.list_graphs() if n.startswith("fix:")]


def test_nothing_to_suggest_when_every_knower_is_used(db):
    load(db, "test:fix-sample", SAMPLE_ORG)
    p = propose(db, "test:fix-sample", "ledger")  # cara is its only knower, on the route
    assert p.fixes == [] and "already on a route" in p.reason


@pytest.mark.parametrize("seed", range(15))
def test_gains_match_networkx(db, seed):
    org = random_org(random.Random(3000 + seed))
    name = f"test:fix{seed}"
    load(db, name, org)
    for s in org["systems"]:
        p = propose(db, name, s["name"])
        assert p.before == nx_number(org, s["name"])
        for fix in p.fixes:
            after = changed(org, fix)
            assert fix.gain == 1, (seed, s["name"], fix)
            assert nx_number(after, s["name"]) == p.before + 1
            also = {c.system: (c.before, c.after) for c in fix.also}
            for other in org["systems"]:
                if other["name"] == s["name"]:
                    continue
                want = (nx_number(org, other["name"]), nx_number(after, other["name"]))
                if want[0] == want[1]:
                    assert other["name"] not in also, (seed, fix, other["name"])
                else:
                    assert also[other["name"]] == want, (seed, fix, other["name"])
    assert not [n for n in db.list_graphs() if n.startswith("fix:")]
    drop(db, name)


@pytest.mark.parametrize("seed", range(10))
def test_applied_fix_matches_networkx(db, seed):
    org = random_org(random.Random(4000 + seed))
    name = f"test:fixapply{seed}"
    g = load(db, name, org)
    fix = next((f for s in org["systems"] for f in propose(db, name, s["name"]).fixes), None)
    if fix is None:
        pytest.skip("no system in this org can be improved by one change")
    apply_fix(db, name, fix)
    after = changed(org, fix)
    stored = dict(g.ro_query("MATCH (s:System) RETURN s.name, s.understudy").result_set)
    assert stored == {s["name"]: nx_number(after, s["name"]) for s in org["systems"]}
    assert critical_in_graph(g) == expected_critical(after)
    drop(db, name)
