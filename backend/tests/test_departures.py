import json
from datetime import UTC, datetime

import pytest

from understudy.derive.departures import _emeritus, departures, emeritus_moves, roster_exits


def at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T00:00:00+00:00")


def test_emeritus_reads_filters_and_refuses_broken_versions():
    assert _emeritus("emeritus_approvers: [Ana]") == {"ana"}
    text = "filters:\n  '.*':\n    emeritus_approvers: [bo]\nemeritus_approvers: [cy]"
    assert _emeritus(text) == {"bo", "cy"}
    assert _emeritus("approvers: [x]") == set()
    assert _emeritus(None) is None  # deleted
    assert _emeritus("approvers: [unclosed") is None
    assert _emeritus("- just\n- a list") is None


@pytest.fixture
def raw(tmp_path):
    def v(oid, day):
        return {"oid": oid, "date": f"{day}T00:00:00Z"}

    mainline = {
        "pkg/OWNERS": [
            v("p4", "2025-09-01"),
            v("p3", "2025-06-01"),  # broken YAML in the middle
            v("p2", "2025-03-01"),
            v("p1", "2023-12-01"),  # baseline
        ],
        "cmd/OWNERS": [v("c1", "2025-01-01")],  # created in the window: baseline only
        "OWNERS_ALIASES": [
            v("a4", "2026-01-01"),
            v("a3", "2025-08-01"),
            v("a2", "2025-05-01"),
            v("a1", "2024-01-01"),
        ],
    }
    blobs = [
        ("pkg/OWNERS", "p1", "approvers: [ana, bo]\nemeritus_approvers: [old]"),
        ("pkg/OWNERS", "p2", "approvers: [bo]\nemeritus_approvers: [old, ana]"),
        ("pkg/OWNERS", "p3", "approvers: [bo\n"),
        ("pkg/OWNERS", "p4", "approvers: []\nemeritus_approvers: [old, ana, bo]"),
        ("cmd/OWNERS", "c1", "emeritus_approvers: [someone]"),
        # cy leaves for good, dee moves from reviewers to approvers (a promotion),
        # eve is dropped and comes back, fay was never in the window's first version.
        ("OWNERS_ALIASES", "a1", "aliases:\n  r: [cy, dee, eve]\n  a: [ana]"),
        ("OWNERS_ALIASES", "a2", "aliases:\n  r: [eve]\n  a: [ana, dee]"),
        ("OWNERS_ALIASES", "a3", "aliases:\n  r: []\n  a: [ana, dee, fay]"),
        ("OWNERS_ALIASES", "a4", "aliases:\n  r: [eve]\n  a: [dee, fay]"),
    ]
    (tmp_path / "owners").mkdir()
    (tmp_path / "owners" / "mainline.json").write_text(json.dumps(mainline))
    (tmp_path / "owners" / "blobs.jsonl").write_text(
        "".join(json.dumps({"path": p, "oid": o, "text": t}) + "\n" for p, o, t in blobs)
    )
    return tmp_path


def test_emeritus_moves(raw):
    assert sorted(emeritus_moves(raw)) == [
        ("ana", at("2025-03-01"), "pkg/OWNERS"),
        ("bo", at("2025-09-01"), "pkg/OWNERS"),  # the broken version in between is skipped
    ]


def test_roster_exits(raw):
    # ana and cy are gone by the last version; dee was promoted, eve came back.
    assert roster_exits(raw) == [
        ("ana", at("2026-01-01"), "OWNERS_ALIASES"),
        ("cy", at("2025-05-01"), "OWNERS_ALIASES"),
    ]


def pr(merged: str, author: str) -> dict:
    return {
        "number": hash((merged, author)) % 10**6,
        "author": author,
        "author_is_bot": False,
        "merged_at": f"{merged}T12:00:00Z",
        "labels": [],
        "files": [],
        "reviews": [],
        "comments": [],
    }


def test_departures_combine_sources_and_activity(raw):
    prs = [pr(d, "Ana") for d in ("2024-05-01", "2024-11-01", "2025-02-01", "2025-04-01")]
    prs += [pr("2025-10-01", "bo")]
    deps = {d.handle: d for d in departures(raw, prs)}

    assert set(deps) == {"ana", "bo", "cy"}
    ana = deps["ana"]
    # First event wins: the emeritus move in March 2025, before the roster exit.
    assert ana.moved_at == at("2025-03-01")
    assert ana.sources == ["pkg/OWNERS", "OWNERS_ALIASES"]
    assert ana.last_active == datetime(2025, 2, 1, 12, tzinfo=UTC)
    assert ana.left_at == ana.last_active
    assert (ana.prs_before, ana.prs_after) == (3, 1)

    # No activity before the move in the data: left_at falls back to the move itself.
    assert deps["cy"].last_active is None and deps["cy"].left_at == at("2025-05-01")
    assert (deps["bo"].prs_before, deps["bo"].prs_after) == (0, 1)
