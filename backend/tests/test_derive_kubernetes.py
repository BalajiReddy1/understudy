from datetime import UTC, date, datetime

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


def test_rosters_use_longest_sig_prefix():
    aliases = {
        "sig-auth-audit-approvers": ["Alice"],
        "sig-node-reviewers": ["bob"],
        "sig-node-cri-approvers": ["cara"],
        "api-reviewers": ["dev"],
    }
    r = k8s.rosters(aliases, {"sig-auth", "sig-node", "sig-node-cri"})
    assert r["sig-auth"] == {"alice"}
    assert r["sig-node"] == {"bob"}
    assert r["sig-node-cri"] == {"cara"}
    assert "api" not in str(r.keys())


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


def test_derive_org_end_to_end():
    owners = {"pkg/kubelet": {"labels": ["sig/node"]}}
    files = ["pkg/kubelet/kubelet.go"]
    prs = [pr(i, "ana", files, reviewers=["Bob", "k8s-ci-robot"]) for i in range(25)]
    prs += [pr(100, "old", files, merged="2024-01-01")]  # outside the 18-month window
    aliases = [(datetime(2024, 1, 1, tzinfo=UTC), {"sig-node-approvers": ["bob"]})]
    org, names = k8s.derive_org(prs, owners, aliases, AS_OF, "salt")

    assert org["systems"] == [{"name": "pkg/kubelet", "teams": ["sig-node"]}]
    handles = {p["handle"] for p in org["people"]}
    assert handles == {names["ana"], names["bob"]}
    assert "k8s-ci-robot" not in names
    assert {p["handle"]: p["teams"] for p in org["people"]}[names["bob"]] == ["sig-node"]
    assert {p["handle"]: p["teams"] for p in org["people"]}[names["ana"]] == []
    assert len(org["knows"]) == 2
    assert [set(pair) for pair in org["collaborates"]] == [{names["ana"], names["bob"]}]
    # No real handle may reach the org (hex pseudonyms can't contain these letters).
    assert "ana" not in str(org) and "bob" not in str(org)
