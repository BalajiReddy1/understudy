import random

import pytest
from nxflow import nx_number, random_org

from understudy.db import drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.engine.refresh import refresh
from understudy.sample import SAMPLE_ORG


def load(db, name, org):
    drop(db, name)
    g = db.select_graph(name)
    write_org(g, org)
    build_flow_layer(g)
    refresh(db, name)
    return g


def critical_in_graph(g) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for system, person in g.ro_query(
        "MATCH (p:Person)-[:CRITICAL]->(s:System) RETURN s.name, p.handle"
    ).result_set:
        out.setdefault(system, set()).add(person)
    return out


def expected_critical(org: dict) -> dict[str, set[str]]:
    """Everyone active whose departure alone lowers the number, checked one by one."""
    active = [p["handle"] for p in org["people"] if not p.get("left_at")]
    out = {}
    for s in org["systems"]:
        n = nx_number(org, s["name"])
        crit = {h for h in active if nx_number(org, s["name"], {h}) < n}
        if crit:
            out[s["name"]] = crit
    return out


def test_sample_org(db):
    g = load(db, "test:crit-sample", SAMPLE_ORG)
    crit = critical_in_graph(g)
    # billing: dev is the only payments approver; any of three knowers can feed dev.
    assert crit["billing"] == {"dev"}
    # ledger: cara is the only knower and dev the only approver.
    assert crit["ledger"] == {"cara", "dev"}
    # auth: gus and ana route alone and dev is the only knower left to feed ben or
    # cara; whichever of ben and cara dev hands to, the other could take over.
    assert crit["auth"] == {"ana", "dev", "gus"}
    assert crit == expected_critical(SAMPLE_ORG)
    assert not [n for n in db.list_graphs() if n.startswith("scratch:")]


@pytest.mark.parametrize("seed", range(20))
def test_matches_networkx(db, seed):
    org = random_org(random.Random(2000 + seed))
    g = load(db, f"test:crit{seed}", org)
    assert critical_in_graph(g) == expected_critical(org), seed
    # Critical people are always on a route.
    loose = g.ro_query(
        "MATCH (p:Person)-[:CRITICAL]->(s) WHERE NOT (p)-[:CARRIES]->(s) RETURN count(p)"
    ).result_set[0][0]
    assert loose == 0
    drop(db, f"test:crit{seed}")
