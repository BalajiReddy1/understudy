import math
from datetime import UTC, datetime, timedelta

import pytest
from test_rehearse import load, no_forks_left

from understudy import backtest as bt
from understudy.derive.departures import Departure
from understudy.sample import SAMPLE_NUMBERS, SAMPLE_ORG


def at(day: str, hour: int = 0) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, tzinfo=UTC)


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def raw_pr(
    number, author, created, merged, files=("pkg/a/x.go",), reviews=(), comments=(), bot=False
):
    """A PR as the fetcher stores it. reviews/comments are (author, time) pairs."""
    return {
        "number": number,
        "author": author,
        "author_is_bot": bot,
        "created_at": iso(created),
        "merged_at": iso(merged),
        "files": list(files),
        "reviews": [
            {"author": a, "bot": False, "at": iso(t), "state": "APPROVED"} for a, t in reviews
        ],
        "comments": [{"author": a, "bot": False, "at": iso(t)} for a, t in comments],
    }


# --- the outcome ---------------------------------------------------------------------


def test_response_is_the_first_review_or_comment_by_someone_else():
    t0 = at("2025-01-01")
    pr = raw_pr(
        1,
        "Ann",
        t0,
        t0 + timedelta(days=2),
        reviews=[("bob", t0 + timedelta(hours=9))],
        comments=[
            ("ann", t0 + timedelta(hours=1)),  # the author, in another case
            ("k8s-ci-robot", t0 + timedelta(hours=2)),  # a bot
            ("cy", t0 + timedelta(hours=5)),
        ],
    )
    assert bt.response_hours(pr) == 5
    pr["comments"] = pr["comments"][:2]
    assert bt.response_hours(pr) == 9  # a review counts as well as a comment
    pr["reviews"] = []
    assert bt.response_hours(pr) is None  # nobody else ever answered


def test_deleted_accounts_are_not_a_response():
    t0 = at("2025-01-01")
    pr = raw_pr(1, "ann", t0, t0 + timedelta(days=1))
    pr["comments"] = [{"author": None, "bot": False, "at": iso(t0 + timedelta(hours=1))}]
    assert bt.response_hours(pr) is None


def test_windows_apply_the_same_merge_rule_on_both_sides():
    start, end = at("2025-01-01"), at("2025-04-01")
    (pr,) = bt.prepare([raw_pr(1, "ann", start, end + bt.GRACE)])
    assert bt.in_window(pr, start, end)  # merged exactly at the grace limit
    pr.merged += timedelta(seconds=1)
    assert not bt.in_window(pr, start, end)  # merged too late to count anywhere
    pr.created, pr.merged = end, end + timedelta(days=1)
    assert not bt.in_window(pr, start, end)  # opened when the window had closed


# --- which departures count ----------------------------------------------------------

FIRST, LAST = at("2024-01-01"), at("2026-10-14")


def history(login: str, days: list[str]) -> list[dict]:
    return [raw_pr(i, login, at(d) - timedelta(days=1), at(d)) for i, d in enumerate(days)]


@pytest.mark.parametrize(
    ("days", "left", "reason"),
    [
        (["2025-01-01", "2025-02-01", "2025-03-01"], "2025-03-01", None),
        (["2025-02-01", "2025-03-01"], "2025-03-01", "fewer than 3 PRs in the 18 months before"),
        (
            ["2025-01-01", "2025-02-01", "2025-03-01", "2025-05-20"],
            "2025-03-01",
            "still active in the 90 days after",
        ),
        # Back after the 90 days is fine: the windows only look 90 days ahead.
        (["2025-01-01", "2025-02-01", "2025-03-01", "2025-06-15"], "2025-03-01", None),
        (
            ["2024-01-10", "2024-02-01", "2024-03-01"],
            "2024-03-01",
            "less than 90 days of data before",
        ),
        (
            ["2026-04-01", "2026-05-01", "2026-06-01"],
            "2026-06-01",
            "too recent to see 90 days after",
        ),
    ],
)
def test_which_departures_count(days, left, reason):
    prs = history("ann", days)
    dep = Departure("ann", at(left), last_active=at(left))
    (event,) = bt.events([dep], prs, {"ann": "contributor-a"}, FIRST, LAST)
    assert (event.person, event.left_at, event.reason) == ("contributor-a", at(left), reason)


def test_someone_with_no_prs_gets_no_pseudonym_and_does_not_count():
    dep = Departure("ghost", at("2025-03-01"))
    (event,) = bt.events([dep], [], {}, FIRST, LAST)
    assert event.person is None and event.reason.startswith("fewer than 3")


# --- the outcome for one departure ---------------------------------------------------

LEFT = at("2025-06-01")


def answered(number, author, created, hours, files=("pkg/a/x.go",)):
    return raw_pr(
        number,
        author,
        created,
        created + timedelta(days=3),
        files=files,
        comments=[("rev", created + timedelta(hours=hours))],
    )


def system_of(path: str) -> str | None:
    return path.split("/")[1] if path.startswith("pkg/") else None


def test_slowdown_is_the_system_change_over_the_project_change():
    before, after = LEFT - timedelta(days=30), LEFT + timedelta(days=30)
    prs = (
        [answered(i, "ann", before, 2) for i in range(5)]  # system a: 2 h before
        + [answered(10 + i, "ann", after, 8) for i in range(5)]  # 8 h after: 4x slower
        + [answered(20 + i, "ann", before, 4, files=["docs/x.md"]) for i in range(5)]
        + [answered(30 + i, "ann", after, 4, files=["docs/x.md"]) for i in range(5)]
        # The person who left: their own PRs count on neither side.
        + [answered(40 + i, "gone", after, 500) for i in range(9)]
    )
    pred = bt.Prediction("a", 2, 1, 4, 3)
    event = bt.Event("contributor-g", LEFT, 10, None)
    (u,) = bt.measure(event, [pred], bt.prepare(prs), system_of, "gone")
    # Project: 10 PRs before (5 at 2 h, 5 at 4 h) has median 3 h; after, 5 at 8 h and
    # 5 at 4 h has median 6 h. So the project slowed 2x and the system 4x.
    assert (u.hours_before, u.hours_after, u.project_ratio) == (2, 8, 2)
    assert u.slowdown == 2 and u.reason is None
    assert (u.prs_before, u.prs_after, u.understudy_after, u.knowers_after) == (5, 5, 1, 3)


def test_a_system_with_too_few_prs_is_kept_but_not_used():
    prs = [answered(i, "ann", LEFT - timedelta(days=5), 2) for i in range(5)] + [
        answered(9 + i, "ann", LEFT + timedelta(days=5), 2) for i in range(4)
    ]
    (u,) = bt.measure(
        bt.Event("p", LEFT, 9, None),
        [bt.Prediction("a", 1, 0, 2, 1)],
        bt.prepare(prs),
        system_of,
        "x",
    )
    assert u.slowdown is None and u.reason == "fewer than 5 PRs in a window"


def test_sweeping_prs_are_left_out():
    files = [f"pkg/s{i}/x.go" for i in range(9)]  # nine systems: more than any one PR may touch
    prs = [answered(i, "ann", LEFT - timedelta(days=5), 2, files) for i in range(5)] + [
        answered(9 + i, "ann", LEFT + timedelta(days=5), 2, files) for i in range(5)
    ]
    prs += [
        answered(20, "ann", LEFT - timedelta(days=5), 2),
        answered(21, "ann", LEFT + timedelta(days=5), 2),
    ]
    (u,) = bt.measure(
        bt.Event("p", LEFT, 9, None),
        [bt.Prediction("s0", 1, 0, 2, 1)],
        bt.prepare(prs),
        system_of,
        "x",
    )
    assert (u.prs_before, u.prs_after) == (0, 0)


# --- statistics ----------------------------------------------------------------------


def test_ranks_share_ties():
    assert bt.ranks([10, 20, 20, 5]) == [2, 3.5, 3.5, 1]


def test_spearman():
    assert bt.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1)
    assert bt.spearman([1, 2, 3, 4], [9, 7, 3, 1]) == pytest.approx(-1)
    # Worked by hand: ranks x = 1,2,3,4,5 and y = 2,1,4,3,5, so d^2 sums to 4 and
    # rho = 1 - 6*4 / (5*24) = 0.8.
    assert bt.spearman([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]) == pytest.approx(0.8)
    assert bt.spearman([1, 1, 1], [1, 2, 3]) is None  # one side constant
    assert bt.spearman([1, 2], [1, 2]) is None  # too few points


def unit(person, u, slowdown, knowers=3):
    return bt.Unit(
        person,
        "2025-01-01",
        f"s{u}{slowdown}",
        u + 1,
        u,
        knowers + 1,
        knowers,
        9,
        9,
        1,
        1,
        1,
        slowdown,
        None,
    )


def test_the_interval_resamples_whole_departures():
    # Two departures, each with one value across all its systems. Any resample is made
    # of whole departures, so a median can only be 1, 4, or between them when both are
    # drawn; resampling single systems could not change that, but it would treat the
    # five systems of one departure as five pieces of evidence.
    units = [unit("a", 1, 1.0) for _ in range(5)] + [unit("b", 1, 4.0)]
    stat = bt._median_of(lambda u: True)
    lo, hi, n = bt.interval(units, stat)
    assert n == bt.RESAMPLES
    assert lo == 1.0 and hi == 4.0
    assert bt.interval(units, stat) == (lo, hi, n)  # the seed makes it repeatable


def test_an_undefined_statistic_gives_no_interval():
    units = [unit("a", 1, 1.0), unit("b", 1, 2.0)]
    assert bt.interval(units, bt._rho("understudy_after"))[:2] == (None, None)


def test_summary_reports_both_measures_and_both_groups():
    units = [
        unit(p, u, s, knowers=k)
        for p, u, s, k in [
            ("a", 0, 3.0, 2),
            ("a", 1, 2.0, 5),
            ("b", 1, 1.5, 1),
            ("b", 2, 1.0, 4),
            ("c", 3, 0.8, 3),
            ("c", 2, 1.1, 6),
            ("d", 4, 0.9, 2),
        ]
    ]
    units.append(
        bt.Unit(
            "e", "2025-01-01", "x", 1, 0, 2, 1, 2, 2, 1, 1, 1, None, "fewer than 5 PRs in a window"
        )
    )
    s = bt.summarise(units)
    assert (s["departures"], s["systems"]) == (4, 7)  # the unit left out isn't counted
    expected = bt.spearman(
        [0, 1, 1, 2, 3, 2, 4], [math.log(x) for x in [3.0, 2.0, 1.5, 1.0, 0.8, 1.1, 0.9]]
    )
    assert s["rho"]["understudy"]["value"] == round(expected, 3) < 0
    assert s["groups"]["low"]["n"] == 3 and s["groups"]["low"]["value"] == 2.0
    assert s["groups"]["high"]["n"] == 4 and s["groups"]["high"]["value"] == 0.95
    for block in [
        s["rho"]["understudy"],
        s["rho"]["knowers"],
        s["groups"]["low"],
        s["groups"]["high"],
    ]:
        assert block["low"] <= block["value"] <= block["high"]


def test_summary_of_nothing_is_empty_not_an_error():
    s = bt.summarise([])
    assert s["systems"] == 0 and s["rho"]["understudy"]["value"] is None


# --- the predictor, on FalkorDB ------------------------------------------------------


def test_prediction_rehearses_the_departure_without_changing_the_graph(db):
    g = load(db, "test:bt-sample", SAMPLE_ORG)
    # dev approves billing and ledger and knows auth.
    got = {p.system: p for p in bt.predict(db, "test:bt-sample", "dev")}
    assert {s: (p.understudy_before, p.understudy_after) for s, p in got.items()} == {
        "billing": (1, 0),
        "ledger": (1, 0),
        "auth": (3, 2),
    }
    assert {s: (p.knowers_before, p.knowers_after) for s, p in got.items()} == {
        "billing": (3, 3),
        "ledger": (1, 1),
        "auth": (3, 2),
    }
    assert bt.predict(db, "test:bt-sample", "nobody") == []
    assert (
        dict(g.ro_query("MATCH (s:System) RETURN s.name, s.understudy").result_set)
        == SAMPLE_NUMBERS
    )
    assert no_forks_left(db)
