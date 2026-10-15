"""Differential tests: FalkorDB's max-flow on our flow layer against two references
built independently from the same org, networkx max-flow and a brute-force search.
Comparing against networkx is how the mixed-capacity bug was caught before the
event, so these stay."""

import random

import pytest
from nxflow import assert_valid_routes, brute_number, nx_number, random_org

from understudy.db import drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.engine.understudy import flow


@pytest.mark.parametrize("seed", range(300))
def test_references_agree(seed):
    """networkx (where chains are possible) and the literal definition (where they
    aren't) must agree, or the claim that chains never add a route is wrong."""
    rng = random.Random(5000 + seed)
    org = random_org(rng, max_people=10)
    for s in org["systems"]:
        for min_score in (0.0, 1.0):
            assert nx_number(org, s["name"], min_score=min_score) == brute_number(
                org, s["name"], min_score=min_score
            ), (seed, s["name"])


def build(db, name, org, min_score):
    drop(db, name)
    g = db.select_graph(name)
    write_org(g, org)
    build_flow_layer(g, min_score=min_score)
    return g


@pytest.mark.parametrize("seed", range(40))
def test_matches_networkx(db, seed):
    rng = random.Random(seed)
    org = random_org(rng)
    name = f"test:rand{seed}"
    g = build(db, name, org, min_score=1.0)

    for s in org["systems"]:
        f = flow(g, s["name"])
        expected = nx_number(org, s["name"], min_score=1.0)
        assert f.number == expected, f"seed {seed}, {s['name']}"
        assert len(f.routes) == f.number
        assert_valid_routes(org, s["name"], f.routes, min_score=1.0)

        # Losing someone who is on none of the routes leaves the number unchanged.
        handles = [p["handle"] for p in org["people"] if not p.get("left_at")]
        for h in rng.sample(handles, min(4, len(handles))):
            if h not in f.carriers:
                assert nx_number(org, s["name"], {h}, min_score=1.0) == expected
    drop(db, name)


@pytest.mark.parametrize("seed", range(20))
def test_matches_brute_force(db, seed):
    rng = random.Random(9000 + seed)
    org = random_org(rng, max_people=10)
    name = f"test:brute{seed}"
    g = build(db, name, org, min_score=0.0)
    for s in org["systems"]:
        assert flow(g, s["name"]).number == brute_number(org, s["name"]), (seed, s["name"])
    drop(db, name)
