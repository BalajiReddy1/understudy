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


def test_flow_layer_edge_counts(db):
    # Guards the 6.0.2 property-map bug: if a MATCH silently returns nothing, a kind
    # of edge goes missing without any error.
    g = load(db)
    counts = dict(g.ro_query("MATCH ()-[e:KNOWLEDGE]->() RETURN e.kind, count(e)").result_set)
    assert counts == {"gate": 7, "knows": 11, "channel": 14, "member": 7}


def test_billing_runs_through_dev(db):
    g = load(db)
    number, carriers = flow(g, "billing")
    assert number == 1
    assert "dev" in carriers and len(carriers) == 2


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
