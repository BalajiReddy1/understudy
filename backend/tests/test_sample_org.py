from understudy.db import drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.engine.understudy import all_systems, flow, understudy
from understudy.sample import SAMPLE_NUMBERS, SAMPLE_ORG


def load(db, name="test:sample"):
    drop(db, name)
    g = db.select_graph(name)
    write_org(g, SAMPLE_ORG)
    build_flow_layer(g)
    return g


def test_numbers(db):
    g = load(db)
    assert {s: understudy(g, s) for s in all_systems(g)} == SAMPLE_NUMBERS


def edge_counts(g, system: str) -> dict:
    return dict(
        g.ro_query(
            """MATCH (s:System {name: $s}) WITH s.flow AS t
               MATCH ()-[e]->() WHERE type(e) = t RETURN e.kind, count(e)""",
            {"s": system},
        ).result_set
    )


def test_flow_layer_edge_counts(db):
    # Counted by hand. Guards the 6.0.2 property-map bug too: if a MATCH silently
    # returns nothing, a kind of edge goes missing without any error.
    g = load(db)
    # billing: ana, ben and cara know it; dev is the only payments approver.
    assert edge_counts(g, "billing") == {"knows": 3, "gate": 4, "member": 1, "channel": 3}
    # auth: dev, gus and ana know it; platform and growth have six approvers. Channels:
    # dev->ben, dev->cara, gus->femi, ana->esi. Not dev->ana: ana knows auth already.
    assert edge_counts(g, "auth") == {"knows": 3, "gate": 7, "member": 6, "channel": 4}
    types = g.ro_query("MATCH (s:System) RETURN s.name, s.flow ORDER BY s.name").result_set
    assert [t for _, t in types] == [f"FLOW_{n}" for n in range(5)]


def test_routes_are_one_or_two_people(db):
    g = load(db)
    auth = flow(g, "auth")
    assert sorted(map(len, auth.routes)) == [1, 1, 2]  # gus and ana alone, dev via one
    assert {r[0] for r in auth.routes if len(r) == 1} == {"gus", "ana"}


def test_a_system_with_no_flow_edges_scores_zero(db):
    org = {
        **SAMPLE_ORG,
        "teams": [*SAMPLE_ORG["teams"], "nobody"],
        "systems": [*SAMPLE_ORG["systems"], {"name": "orphan", "teams": ["nobody"]}],
    }
    drop(db, "test:sample")
    g = db.select_graph("test:sample")
    write_org(g, org)
    build_flow_layer(g)
    f = flow(g, "orphan")
    assert f.number == 0 and f.routes == []
    assert understudy(g, "billing") == 1


def test_billing_runs_through_dev(db):
    g = load(db)
    f = flow(g, "billing")
    assert f.number == 1
    # One route: a knower (ana, ben or cara) hands it to dev, the only payments member.
    assert len(f.routes) == 1
    assert f.routes[0][0] in {"ana", "ben", "cara"} and f.routes[0][1:] == ["dev"]
    assert "dev" in f.carriers and len(f.carriers) == 2


def test_rehearsal_on_a_copy_leaves_the_original_alone(db):
    g = load(db)
    drop(db, "test:sample-fork")
    fork = g.copy("test:sample-fork")
    fork.query(
        """MATCH (p:Person {handle: $h}) SET p.left_at = '2026-10-15'
           WITH p.handle AS h MATCH (port:Port) WHERE port.person = h DETACH DELETE port""",
        {"h": "dev"},
    )
    assert understudy(fork, "billing") == 0
    assert understudy(fork, "ledger") == 0
    assert understudy(fork, "auth") == 2
    assert {s: understudy(g, s) for s in all_systems(g)} == SAMPLE_NUMBERS
