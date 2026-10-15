"""Differential test: FalkorDB's max-flow on our flow layer against networkx on the
same node-split graph, built independently here. This is the test that caught the
mixed-capacity bug before the event, so it stays."""

import random

import pytest
from nxflow import assert_valid_routes, nx_number, random_org

from understudy.db import drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.engine.understudy import flow


@pytest.mark.parametrize("seed", range(40))
def test_matches_networkx(db, seed):
    rng = random.Random(seed)
    org = random_org(rng)
    name = f"test:rand{seed}"
    drop(db, name)
    g = db.select_graph(name)
    write_org(g, org)
    build_flow_layer(g, min_score=1.0)

    for s in org["systems"]:
        f = flow(g, s["name"])
        expected = nx_number(org, s["name"], min_score=1.0)
        assert f.number == expected, f"seed {seed}, {s['name']}"
        assert_valid_routes(org, s["name"], f.routes, min_score=1.0)

        # Carriers must be exactly the people whose loss could matter: removing anyone
        # who is not a carrier leaves the number unchanged.
        handles = [p["handle"] for p in org["people"] if not p.get("left_at")]
        for h in rng.sample(handles, min(4, len(handles))):
            if h not in f.carriers:
                assert nx_number(org, s["name"], {h}, min_score=1.0) == expected
    drop(db, name)
