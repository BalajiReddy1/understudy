import copy
import random

import pytest
from nxflow import nx_number, random_org
from test_critical import critical_in_graph, expected_critical

from understudy.db import drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.engine.refresh import refresh
from understudy.engine.rehearse import depart
from understudy.engine.sessions import SessionGone, Sessions
from understudy.sample import SAMPLE_NUMBERS, SAMPLE_ORG

BASE = "test:sess-base"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def load(db, name, org):
    drop(db, name)
    g = db.select_graph(name)
    write_org(g, org)
    build_flow_layer(g)
    refresh(db, name)
    return g


@pytest.fixture
def base(db):
    load(db, BASE, SAMPLE_ORG)
    yield db
    for name in db.list_graphs():
        if name.startswith("session:"):
            drop(db, name)


def numbers(db, graph):
    return dict(
        db.select_graph(graph).ro_query("MATCH (s:System) RETURN s.name, s.understudy").result_set
    )


def test_a_session_is_a_private_copy(base):
    db = base
    sessions = Sessions(lambda: db, BASE)
    a, b = sessions.start(), sessions.start()
    depart(db, a.graph, ["dev"])
    assert numbers(db, a.graph)["billing"] == 0
    assert numbers(db, b.graph) == SAMPLE_NUMBERS
    assert numbers(db, BASE) == SAMPLE_NUMBERS


def test_reset_and_end(base):
    db = base
    sessions = Sessions(lambda: db, BASE)
    s = sessions.start()
    depart(db, s.graph, ["dev"])
    s.changes.append({"type": "depart", "people": ["dev"]})
    sessions.reset(s.id)
    assert numbers(db, s.graph) == SAMPLE_NUMBERS and s.changes == []
    sessions.end(s.id)
    assert s.graph not in db.list_graphs()
    with pytest.raises(SessionGone):
        sessions.get(s.id)


def test_idle_sessions_expire(base):
    db, clock = base, Clock()
    sessions = Sessions(lambda: db, BASE, ttl=100, clock=clock)
    old = sessions.start()
    clock.now = 60
    fresh = sessions.start()
    clock.now = 130  # old has been idle 130 s, fresh only 70 s
    sessions.get(fresh.id)
    with pytest.raises(SessionGone):
        sessions.get(old.id)
    assert old.graph not in db.list_graphs()
    assert fresh.graph in db.list_graphs()


def test_the_cap_drops_the_least_recently_used(base):
    db, clock = base, Clock()
    sessions = Sessions(lambda: db, BASE, cap=2, clock=clock)
    first = sessions.start()
    clock.now = 1
    second = sessions.start()
    clock.now = 2
    sessions.get(first.id)  # first is now the more recently used
    clock.now = 3
    third = sessions.start()
    assert set(sessions.items) == {first.id, third.id}
    assert second.graph not in db.list_graphs()


def test_orphans_from_a_restart_are_dropped(base):
    db = base
    db.select_graph(BASE).copy("session:leftover")
    Sessions(lambda: db, BASE).drop_orphans()
    assert "session:leftover" not in db.list_graphs()


@pytest.mark.parametrize("seed", range(12))
def test_departures_in_a_session_match_networkx(db, seed):
    rng = random.Random(6000 + seed)
    org = random_org(rng)
    name = f"test:depart{seed}"
    g = load(db, name, org)
    active = [p["handle"] for p in org["people"] if not p.get("left_at")]
    gone: set[str] = set()
    for _ in range(2):  # two departures in a row, the second on top of the first
        leaving = set(rng.sample(sorted(set(active) - gone), 2))
        depart(db, name, sorted(leaving))
        gone |= leaving
        after = copy.deepcopy(org)
        for p in after["people"]:
            if p["handle"] in gone:
                p["left_at"] = "2026-10-15"
        stored = dict(g.ro_query("MATCH (s:System) RETURN s.name, s.understudy").result_set)
        assert stored == {s["name"]: nx_number(after, s["name"]) for s in org["systems"]}
        assert critical_in_graph(g) == expected_critical(after), seed
    drop(db, name)
