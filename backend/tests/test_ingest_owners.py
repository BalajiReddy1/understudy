from understudy.ingest.owners import mainline_versions


def test_mainline_orders_by_merge_time_not_branch_commit_time():
    # Two PRs edit the same file at once. PR A's commit is older but merged later.
    history = {
        "OWNERS_ALIASES": [
            {"oid": "b1", "date": "2026-07-24T00:00:00Z"},  # PR B, merged Aug 27
            {"oid": "a1", "date": "2026-07-23T00:00:00Z"},  # PR A, merged Aug 31
            {"oid": "a0", "date": "2026-07-20T00:00:00Z"},  # also PR A
            {"oid": "d0", "date": "2023-12-01T00:00:00Z"},  # pushed directly, no PR
        ]
    }
    landed = {
        "a0": ("mA", "2026-08-31T00:00:00Z"),
        "a1": ("mA", "2026-08-31T00:00:00Z"),
        "b1": ("mB", "2026-08-27T00:00:00Z"),
        "d0": None,
    }
    assert mainline_versions(history, landed) == {
        "OWNERS_ALIASES": [
            {"oid": "mA", "date": "2026-08-31T00:00:00Z"},
            {"oid": "mB", "date": "2026-08-27T00:00:00Z"},
            {"oid": "d0", "date": "2023-12-01T00:00:00Z"},
        ]
    }
