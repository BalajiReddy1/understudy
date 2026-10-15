"""Differential test: FalkorDB's max-flow on our flow layer against networkx on the
same node-split graph, built independently here. This is the test that caught the
mixed-capacity bug before the event, so it stays."""

import random

import pytest
from nxflow import nx_number

from understudy.db import drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.engine.understudy import flow


def random_org(rng: random.Random) -> dict:
    teams = [f"t{i}" for i in range(rng.randint(2, 5))]
    handles = [f"p{i}" for i in range(rng.randint(6, 25))]
    people = [
        {
            "handle": h,
            "teams": rng.sample(teams, rng.choice([0, 1, 1, 1, 2])),
            "left_at": "2025-01-01" if rng.random() < 0.1 else None,
        }
        for h in handles
    ]
    systems = [
        {"name": f"s{i}", "teams": rng.sample(teams, rng.randint(1, 2))}
        for i in range(rng.randint(2, 6))
    ]
    knows = [
        {"person": h, "system": s["name"], "score": round(rng.uniform(0.5, 3), 2)}
        for s in systems
        for h in rng.sample(handles, rng.randint(0, min(6, len(handles))))
    ]
    pairs = {
        tuple(rng.sample(handles, 2)) for _ in range(rng.randint(len(handles), 3 * len(handles)))
    }
    # Half the orgs use the Kubernetes shape (team dicts, extra system properties), so
    # both ways write_org can be called go through the comparison.
    if rng.random() < 0.5:
        teams = [{"name": t, "kind": "approvers"} for t in teams]
        for s in systems:
            s |= {"sigs": ["sig-x"], "emeritus_approvers": 1, "inactive_owners": None}
    return {
        "teams": teams,
        "systems": systems,
        "people": people,
        "knows": knows,
        "collaborates": [list(p) for p in pairs],
    }


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
        number, carriers = flow(g, s["name"])
        expected = nx_number(org, s["name"], min_score=1.0)
        assert number == expected, f"seed {seed}, {s['name']}"

        # Carriers must be exactly the people whose loss could matter: removing anyone
        # who is not a carrier leaves the number unchanged.
        handles = [p["handle"] for p in org["people"] if not p.get("left_at")]
        for h in rng.sample(handles, min(4, len(handles))):
            if h not in carriers:
                assert nx_number(org, s["name"], {h}, min_score=1.0) == expected
        if carriers:
            assert len(carriers) >= number
    drop(db, name)
