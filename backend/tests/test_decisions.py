import json
from datetime import UTC, datetime

import pytest

from understudy.agent.tools import Context, call
from understudy.db import drop
from understudy.derive.decisions import link_decisions, load_keps
from understudy.derive.flow import build_flow_layer, write_org

KEPS = [
    (
        "keps/sig-node/1287-in-place-update/kep.yaml",
        (
            "title: In-place update of pod resources\nkep-number: 1287\nstatus: implementable\n"
            "stage: beta\nowning-sig: sig-node\napprovers:\n  - '@Dchen'\n  - '@gone-person'\n"
        ),
    ),
    ("keps/sig-auth/42-old/kep.yaml", "title: Old idea\nkep-number: '42'\nstatus: withdrawn\n"),
    ("keps/NNNN-kep-template/kep.yaml", "title: KEP Template\nkep-number: NNNN\n"),
    ("keps/sig-x/9-broken/kep.yaml", "title: [unclosed\n"),
    (
        "keps/sig-x/10-bad-date/kep.yaml",
        "title: Bad date\nkep-number: 10\ncreation-date: 2020-13-01\n",
    ),
]


@pytest.fixture
def raw(tmp_path):
    (tmp_path / "keps").mkdir()
    (tmp_path / "keps" / "keps.jsonl").write_text(
        "".join(json.dumps({"path": p, "oid": "master", "text": t}) + "\n" for p, t in KEPS)
    )
    return tmp_path


def test_load_keps(raw):
    keps = load_keps(raw)
    assert set(keps) == {1287, 42}  # the template and the broken file are skipped
    k = keps[1287]
    assert k["title"] == "In-place update of pod resources" and k["stage"] == "beta"
    assert k["approvers"] == ["dchen", "gone-person"]
    assert k["url"].endswith("/tree/master/keps/sig-node/1287-in-place-update")


def pr(n, files, refs, merged="2026-01-01"):
    return {"number": n, "files": files, "kep_refs": refs, "merged_at": f"{merged}T00:00:00Z"}


def test_linking_decisions_to_systems(raw):
    keps = load_keps(raw)
    prs = [
        pr(1, ["pkg/kubelet/a.go", "pkg/api/b.go"], [1287]),
        pr(2, ["pkg/kubelet/c.go"], [1287, 9999]),  # 9999 isn't a real KEP
        pr(3, ["pkg/kubelet/d.go"], [42], merged="2026-12-01"),  # after the as-of date
        pr(4, ["docs/readme.md"], [42]),  # touches no system
    ]
    system_of = {"pkg/kubelet": "pkg/kubelet", "pkg/api": "pkg/api"}.get
    decisions = link_decisions(
        prs,
        lambda f: system_of(f.rsplit("/", 1)[0]),
        keps,
        {"dchen": "contributor-0d0d"},
        datetime(2026, 10, 14, tzinfo=UTC),
    )
    assert [d["number"] for d in decisions] == [1287]
    d = decisions[0]
    assert d["systems"] == [{"name": "pkg/api", "prs": 1}, {"name": "pkg/kubelet", "prs": 2}]
    # Approvers who never appear in the PR data can't be named, only counted.
    assert d["approvers"] == ["contributor-0d0d"] and d["approvers_total"] == 2


def test_why(db):
    org = {
        "teams": ["t"],
        "systems": [{"name": "pkg/kubelet", "teams": ["t"]}, {"name": "pkg/api", "teams": ["t"]}],
        "people": [{"handle": "contributor-0d0d", "teams": ["t"]}],
        "knows": [],
        "collaborates": [],
        "decisions": [
            {
                "number": 1287,
                "title": "In-place update of pod resources",
                "status": "implementable",
                "stage": "beta",
                "sig": "sig-node",
                "url": "https://example.test/1287",
                "approvers": ["contributor-0d0d", "contributor-dead"],
                "approvers_total": 3,
                "systems": [{"name": "pkg/kubelet", "prs": 2}],
            }
        ],
    }
    drop(db, "test:why")
    g = db.select_graph("test:why")
    write_org(g, org)
    build_flow_layer(g)
    assert g.ro_query("MATCH (:Decision)-[r:SHAPED]->() RETURN count(r)").result_set[0][0] == 1

    ctx = Context(db, "test:why")
    r = call(ctx, "why", {"system": "kubelet"})
    assert r.status == "ok"
    d = r.data["decisions"][0]
    assert d["kep"] == 1287 and d["prs"] == 2
    assert d["approvers_still_around"] == ["contributor-0d0d"] and d["approvers_total"] == 3
    assert "KEP-1287" in r.summary and "1 of its 3 approvers" in r.summary

    none = call(ctx, "why", {"system": "pkg/api"})
    assert none.status == "not_in_graph" and none.data["decisions"] == []
    drop(db, "test:why")
