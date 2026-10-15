import json
from datetime import UTC, date, datetime

import pytest

from understudy.derive import kubernetes as k8s
from understudy.derive.people import is_bot, pseudonyms

AS_OF = date(2026, 10, 14)


def pr(number, author, files, *, reviewers=(), labels=("sig/node",), merged="2026-09-01"):
    return {
        "number": number,
        "author": author,
        "author_is_bot": False,
        "merged_at": f"{merged}T12:00:00Z",
        "labels": list(labels),
        "files": list(files),
        "reviews": [],
        "comments": [{"author": r, "bot": False, "at": f"{merged}T12:00:00Z"} for r in reviewers],
    }


def test_bots():
    assert is_bot("k8s-ci-robot")
    assert is_bot("k8s-triage-robot")
    assert is_bot("renovate[bot]")
    assert is_bot(None)
    assert is_bot("someone", typed_bot=True)
    assert not is_bot("liggitt")


def test_pseudonyms_are_unique_and_stable():
    logins = {f"user{i}" for i in range(3000)}
    a = pseudonyms(logins, "salt")
    assert len(set(a.values())) == 3000
    assert a == pseudonyms(logins, "salt")
    assert a != pseudonyms(logins, "other salt")
    assert all(name.startswith("contributor-") for name in a.values())


# --- reading OWNERS files -------------------------------------------------------------

FILES = {
    "": {"filters": {".*": {"approvers": ["root-approvers"]}, r"go\.mod$": {"approvers": ["dep"]}}},
    "pkg": {"approvers": ["pat"], "labels": ["sig/node"]},
    "pkg/kubelet": {"approvers": ["Kim", "node-approvers"], "emeritus_approvers": ["old1", "old2"]},
    "pkg/kubelet/cm": {"reviewers": ["rev"]},  # lists no approvers: parent approves
    "pkg/kubelet/cm/cpu": {"approvers": ["gone"]},  # its only approver went quiet
    "pkg/kubelet/cm/dev": {"approvers": [], "emeritus_approvers": ["a", "b"]},
    "pkg/api": {
        "options": {"no_parent_owners": True},
        "filters": {
            ".*": {"approvers": ["api-approvers"], "labels": ["sig/api-machinery"]},
            r"types\.go$": {"approvers": ["typist"]},
        },
    },
    "pkg/api/v1": {"reviewers": ["x"]},
    "pkg/locked": {"options": {"no_parent_owners": True}, "approvers": ["gone"]},
}
ALIASES = {"root-approvers": ["Root1"], "node-approvers": ["nora", "NED"], "api-approvers": ["ann"]}
ACTIVE = {"root1", "pat", "kim", "nora", "ned", "ann", "typist", "rev"}
NODE = {"kim", "nora", "ned"}


@pytest.fixture
def tree():
    return k8s.OwnersTree(FILES, ALIASES)


def test_approvers_expand_aliases_and_lowercase(tree):
    assert tree.approvers("pkg/kubelet", ACTIVE) == ("pkg/kubelet", NODE, None)


def test_file_without_approvers_defers_to_parent(tree):
    assert tree.approvers("pkg/kubelet/cm", ACTIVE) == ("pkg/kubelet", NODE, None)
    assert tree.approvers("pkg/kubelet/cm/dev", ACTIVE) == ("pkg/kubelet", NODE, None)


def test_quiet_approvers_fall_back_and_are_recorded(tree):
    assert tree.approvers("pkg/kubelet/cm/cpu", ACTIVE) == (
        "pkg/kubelet",
        NODE,
        "pkg/kubelet/cm/cpu",
    )


def test_catch_all_filter_counts_and_path_filters_do_not(tree):
    assert tree.approvers("pkg/api/v1", ACTIVE) == ("pkg/api", {"ann"}, None)
    assert tree.approvers("cmd/kubectl", ACTIVE) == ("", {"root1"}, None)


def test_no_parent_owners_stops_the_fallback(tree):
    assert tree.approvers("pkg/locked", ACTIVE) == ("pkg/locked", set(), None)


def test_sigs_and_emeritus(tree):
    assert tree.sigs("pkg/kubelet/cm") == ["sig-node"]
    assert tree.sigs("pkg/api/v1") == ["sig-api-machinery"]
    assert tree.sigs("cmd") == []
    assert tree.emeritus("pkg/kubelet") == 2
    assert tree.emeritus("pkg/kubelet/cm") == 0
    assert tree.emeritus("nowhere") == 0


@pytest.fixture
def raw(tmp_path):
    def c(oid, day):
        return {"oid": oid, "date": f"{day}T00:00:00Z"}

    history = {
        "pkg/OWNERS": [c("p3", "2025-06-01"), c("p2", "2024-06-01"), c("p1", "2023-12-01")],
        "new/OWNERS": [c("n1", "2025-01-01")],
        "gone/OWNERS": [c("g2", "2025-01-01"), c("g1", "2024-01-01")],
        "bad/OWNERS": [c("b1", "2024-01-01")],
        "OWNERS_ALIASES": [c("a2", "2025-03-01"), c("a1", "2024-01-01")],
    }
    blobs = [
        ("pkg/OWNERS", "p1", "approvers: [a]"),
        ("pkg/OWNERS", "p2", "approvers: [b]"),
        ("pkg/OWNERS", "p3", "approvers: [c]"),
        ("new/OWNERS", "n1", "approvers: [n]"),
        ("gone/OWNERS", "g1", "approvers: [g]"),
        ("gone/OWNERS", "g2", None),  # this commit deleted the file
        ("bad/OWNERS", "b1", "approvers: [unclosed"),
        ("OWNERS_ALIASES", "a1", "aliases: {x: [old]}"),
        ("OWNERS_ALIASES", "a2", "aliases: {x: [new]}"),
    ]
    (tmp_path / "owners").mkdir()
    (tmp_path / "owners" / "history.json").write_text(json.dumps(history))
    (tmp_path / "owners" / "blobs.jsonl").write_text(
        "".join(json.dumps({"path": p, "oid": o, "text": t}) + "\n" for p, o, t in blobs)
    )
    return tmp_path


def test_owners_as_of_a_date(raw):
    early = k8s.load_owners(raw, date(2023, 12, 15))
    assert early["pkg"] == {"approvers": ["a"]} and "new" not in early

    mid = k8s.load_owners(raw, date(2024, 12, 31))
    assert mid["pkg"] == {"approvers": ["b"]}
    assert "new" not in mid
    assert mid["gone"] == {"approvers": ["g"]}
    assert mid["bad"] == {}  # unparseable YAML reads as an empty file, not a crash

    late = k8s.load_owners(raw, date(2025, 7, 1))
    assert late["pkg"] == {"approvers": ["c"]} and "new" in late and "gone" not in late
    assert k8s.load_owners(raw) == late


def test_aliases_as_of_a_date(raw):
    versions = k8s.load_aliases(raw)
    assert k8s.aliases_at(versions, k8s.end_of(date(2023, 6, 1))) == {}
    assert k8s.aliases_at(versions, k8s.end_of(date(2024, 6, 1))) == {"x": ["old"]}
    assert k8s.aliases_at(versions, k8s.end_of(date(2025, 6, 1))) == {"x": ["new"]}


# --- systems and the derived org ------------------------------------------------------


def test_thin_directories_fold_into_parent_and_sweeps_do_not_count():
    owners = {"pkg/kubelet": {}, "pkg/kubelet/cm": {}, "pkg/proxy": {}}
    prs = [pr(i, "ana", ["pkg/kubelet/x.go"]) for i in range(15)]
    prs += [pr(100 + i, "ana", ["pkg/kubelet/cm/y.go"]) for i in range(10)]
    prs += [pr(200 + i, "ana", ["pkg/proxy/z.go"]) for i in range(19)]
    # A sweep touching every directory 10 times must not lift pkg/proxy to 20 PRs.
    many = [f"pkg/d{j}/f.go" for j in range(10)]
    owners |= {f"pkg/d{j}": {} for j in range(10)}
    prs += [pr(300 + i, "ana", ["pkg/proxy/z.go", *many]) for i in range(10)]
    until = datetime(2026, 10, 14, tzinfo=UTC)
    m = k8s.SystemMap(owners, prs, until.replace(year=2025), until)
    assert m.systems == ["pkg/kubelet"]
    assert m.system_of("pkg/kubelet/cm/y.go") == "pkg/kubelet"
    assert m.system_of("pkg/proxy/z.go") is None


def test_excluded_directories_never_become_systems():
    owners = {"pkg/features": {}, "pkg/kubelet": {}}
    prs = [pr(i, "ana", ["pkg/features/f.go", "pkg/kubelet/k.go"]) for i in range(30)]
    until = datetime(2026, 10, 14, tzinfo=UTC)
    m = k8s.SystemMap(owners, prs, until.replace(year=2025), until)
    assert m.systems == ["pkg/kubelet"]
    assert m.system_of("pkg/features/f.go") is None


def test_derive_org_end_to_end():
    owners = {
        "": {"approvers": ["root"]},
        "pkg/kubelet": {"approvers": ["bob", "gone"], "labels": ["sig/node"]},
        "pkg/kubelet/cm": {"approvers": ["quiet"]},
    }
    kubelet, cm = ["pkg/kubelet/kubelet.go"], ["pkg/kubelet/cm/cm.go"]
    prs = [pr(i, "ana", kubelet, reviewers=["Bob", "k8s-ci-robot"]) for i in range(25)]
    prs += [pr(100 + i, "ana", cm, reviewers=["bob"]) for i in range(25)]
    # quiet shows up twice: below the 3 PRs an approver needs to count as active.
    prs += [pr(200 + i, "ana", cm, reviewers=["quiet"]) for i in range(2)]
    prs += [pr(300, "old", kubelet, merged="2024-01-01")]  # outside the 18-month window
    aliases = [(datetime(2024, 1, 1, tzinfo=UTC), {})]
    org, names = k8s.derive_org(prs, owners, aliases, AS_OF, "salt")

    assert org["teams"] == [{"name": "pkg/kubelet/OWNERS", "kind": "approvers"}]
    assert org["systems"] == [
        {
            "name": "pkg/kubelet",
            "teams": ["pkg/kubelet/OWNERS"],
            "sigs": ["sig-node"],
            "emeritus_approvers": 0,
            "inactive_owners": None,
        },
        {
            "name": "pkg/kubelet/cm",
            "teams": ["pkg/kubelet/OWNERS"],
            "sigs": ["sig-node"],
            "emeritus_approvers": 0,
            "inactive_owners": "pkg/kubelet/cm/OWNERS",
        },
    ]
    teams = {p["handle"]: p["teams"] for p in org["people"]}
    assert teams[names["bob"]] == ["pkg/kubelet/OWNERS"]
    assert teams[names["ana"]] == []
    assert teams[names["quiet"]] == []  # in the graph as a collaborator, not an approver
    assert "k8s-ci-robot" not in names
    assert names["old"] not in teams  # no activity in the window, so not in the graph
    knows = {(k["person"], k["system"]) for k in org["knows"]}
    assert knows == {
        (names["ana"], "pkg/kubelet"),
        (names["ana"], "pkg/kubelet/cm"),
        (names["bob"], "pkg/kubelet"),
        (names["bob"], "pkg/kubelet/cm"),
    }
    # No real handle may reach the org (these letters can't appear in hex pseudonyms).
    for handle in ("ana", "bob", "quiet", "gone", "root", "old"):
        assert f"'{handle}'" not in str(org)
