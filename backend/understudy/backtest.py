"""The backtest behind /proof: did real departures slow reviews down where the
understudy number said they would?

The method was fixed before any result was computed, and is reported whatever comes
out. A small sample is the honest size of the data, not a reason to stretch it.

  events     Departures from OWNERS history (derive/departures.py) where the person
             had at least 3 PRs in the 18 months before (the bar an approver needs to
             count as active) and no PR at all in the 90 days after. People who only
             stepped back from one area and kept working are not departures.
             The departure date D is their last activity before the move.
  windows    PRs created in the 90 days before D and in the 90 days after. A PR counts
             only if it merged within 60 days of its window closing, the same rule on
             both sides, since only merged PRs were pulled. Both windows have to sit
             inside the data.
  predictor  The org graph is rebuilt as it stood on D, in FalkorDB, and the person is
             rehearsed off it. Each system they knew or approved is one unit, scored by
             the understudy number left.
  baseline   The headcount: how many people still know the system.
  outcome    Median hours from a PR being opened to the first review or comment by
             someone other than its author and not a bot, for PRs touching the system,
             after D over before D. Divided by the same ratio for every PR in the
             project, so release cycles and holidays cancel out. A unit needs at
             least 5 PRs in each window.
  report     Spearman's rho for each measure against the outcome, and the median
             outcome for systems left at 1 or below versus 2 or more. 95% intervals
             come from resampling whole departures (the systems of one departure are
             not independent), 2,000 times with a fixed seed.
"""

import math
import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median

from falkordb import FalkorDB

from .db import drop
from .derive.departures import Departure
from .derive.flow import build_flow_layer, write_org
from .derive.kubernetes import (
    KNOWS_WINDOW,
    MAX_SYSTEMS_PER_PR,
    MIN_ACTIVE_PRS,
    SystemMap,
    _participants,
    _time,
    derive_org,
    end_of,
    load_owners,
)
from .derive.people import is_bot
from .engine.refresh import refresh
from .engine.rehearse import rehearse

WINDOW = timedelta(days=90)
GRACE = timedelta(days=60)  # how long after a window closes a PR may still merge
MIN_PRS = 5
LOW = 1  # "left at 1 or below" is the low group
RESAMPLES = 2000
SEED = 20261016
PREFIX = "backtest:"


# --- the outcome ---------------------------------------------------------------------


@dataclass
class PR:
    number: int
    author: str  # lowercased
    bot: bool
    created: datetime
    merged: datetime
    response_hours: float | None
    files: list[str]


def response_hours(pr: dict) -> float | None:
    """Hours from opening to the first review or comment by someone else, not a bot."""
    author = (pr["author"] or "").lower()
    times = [
        _time(x["at"])
        for x in pr["reviews"] + pr["comments"]
        if not is_bot(x["author"], x["bot"]) and x["author"].lower() != author
    ]
    if not times:
        return None
    return (min(times) - _time(pr["created_at"])).total_seconds() / 3600


def prepare(prs: list[dict]) -> list[PR]:
    return [
        PR(
            number=p["number"],
            author=(p["author"] or "").lower(),
            bot=is_bot(p["author"], p["author_is_bot"]),
            created=_time(p["created_at"]),
            merged=_time(p["merged_at"]),
            response_hours=response_hours(p),
            files=p["files"],
        )
        for p in prs
    ]


def in_window(pr: PR, start: datetime, end: datetime) -> bool:
    return start <= pr.created < end and pr.merged <= end + GRACE


def median_response(prs: list[PR]) -> tuple[float | None, int]:
    hours = [p.response_hours for p in prs if p.response_hours is not None]
    return (median(hours) if hours else None), len(hours)


# --- which departures count ----------------------------------------------------------


@dataclass
class Event:
    person: str | None  # pseudonym; None for someone with no PR in the data at all
    left_at: datetime
    prs_before: int
    reason: str | None  # why it was left out, or None if it counts


def events(
    deps: list[Departure], prs: list[dict], names: dict[str, str], first: datetime, last: datetime
) -> list[Event]:
    """Every departure with the reason it was left out, if it was. `first` and `last`
    bound the data: the earliest and latest merge pulled."""
    activity = defaultdict(list)
    for pr in prs:
        for person in _participants(pr):
            activity[person].append(_time(pr["merged_at"]))

    out = []
    for d in deps:
        when = d.left_at
        times = activity[d.handle]
        before = sum(when - KNOWS_WINDOW < t <= when for t in times)
        after = sum(when < t <= when + WINDOW for t in times)
        if before < MIN_ACTIVE_PRS:
            reason = f"fewer than {MIN_ACTIVE_PRS} PRs in the 18 months before"
        elif after:
            reason = "still active in the 90 days after"
        elif when - WINDOW < first:
            reason = "less than 90 days of data before"
        elif when + WINDOW + GRACE > last:
            reason = "too recent to see 90 days after"
        else:
            reason = None
        out.append(Event(names.get(d.handle), when, before, reason))
    return out


# --- the predictor, on a snapshot in FalkorDB ----------------------------------------


@dataclass
class Prediction:
    system: str
    understudy_before: int
    understudy_after: int
    knowers_before: int
    knowers_after: int


def predict(db: FalkorDB, graph: str, person: str) -> list[Prediction]:
    """Every system the person knows or approves, before and after rehearsing their
    departure on a fork. The graph itself is left as it was."""
    g = db.select_graph(graph)
    knows = {
        r[0]
        for r in g.ro_query(
            "MATCH (:Person {handle: $p})-[:KNOWS]->(s:System) RETURN s.name", {"p": person}
        ).result_set
    }
    approves = {
        r[0]
        for r in g.ro_query(
            """MATCH (:Person {handle: $p})-[:MEMBER_OF]->(:Team)-[:DEPENDS_ON]->(s:System)
               RETURN DISTINCT s.name""",
            {"p": person},
        ).result_set
    }
    systems = sorted(knows | approves)
    if not systems:
        return []
    rows = g.ro_query(
        """MATCH (s:System) WHERE s.name IN $s
           OPTIONAL MATCH (s)<-[:KNOWS]-(k:Person) WHERE k.left_at IS NULL
           WITH s, count(k) AS knowers
           RETURN s.name, s.understudy, knowers""",
        {"s": systems},
    ).result_set
    after = {c.system: c.after for c in rehearse(db, graph, [person]).changes}
    return [
        Prediction(
            system=name,
            understudy_before=number,
            understudy_after=after.get(name, number),
            knowers_before=knowers,
            knowers_after=knowers - (name in knows),
        )
        for name, number, knowers in rows
    ]


def snapshot(
    db: FalkorDB, graph: str, prs: list[dict], raw: Path, aliases: list, as_of: date, salt: str
) -> SystemMap:
    """Write the org as it stood at the end of `as_of` to `graph`, numbers and all, and
    return that day's map from files to systems."""
    owners = load_owners(raw, as_of)
    org, _ = derive_org(prs, owners, aliases, as_of, salt)
    drop(db, graph)
    g = db.select_graph(graph)
    write_org(g, org)
    build_flow_layer(g)
    refresh(db, graph)
    until = end_of(as_of)
    return SystemMap(owners, prs, until - KNOWS_WINDOW, until)


# --- one unit: a system one departure touched ----------------------------------------


@dataclass
class Unit:
    person: str
    left_at: str
    system: str
    understudy_before: int
    understudy_after: int
    knowers_before: int
    knowers_after: int
    prs_before: int
    prs_after: int
    hours_before: float | None
    hours_after: float | None
    project_ratio: float
    slowdown: float | None  # (after / before) / project_ratio; None if not measurable
    reason: str | None  # why it was left out, or None if it counts


def measure(
    event: Event,
    predictions: list[Prediction],
    prs: list[PR],
    system_of: Callable[[str], str | None],
    person_login: str,
) -> list[Unit]:
    """The outcome for each predicted system, against the whole project's change over
    the same two windows."""
    when = event.left_at
    windows = {"before": (when - WINDOW, when), "after": (when, when + WINDOW)}
    touched: dict[int, set[str]] = {}
    pool = {k: [] for k in windows}
    for pr in prs:
        if pr.bot or pr.author == person_login:
            continue
        side = next((k for k, (a, b) in windows.items() if in_window(pr, a, b)), None)
        if side is None:
            continue
        systems = {s for f in pr.files if (s := system_of(f))}
        if len(systems) > MAX_SYSTEMS_PER_PR:
            continue  # sweeps say nothing about one system
        touched[pr.number] = systems
        pool[side].append(pr)

    project_before, _ = median_response(pool["before"])
    project_after, _ = median_response(pool["after"])
    project = project_after / project_before

    units = []
    for p in predictions:
        before, n_before = median_response(
            [x for x in pool["before"] if p.system in touched[x.number]]
        )
        after, n_after = median_response(
            [x for x in pool["after"] if p.system in touched[x.number]]
        )
        reason = None
        if min(n_before, n_after) < MIN_PRS:
            reason = f"fewer than {MIN_PRS} PRs in a window"
        elif not before or not after:
            reason = "a median response time of zero"
        units.append(
            Unit(
                person=event.person,
                left_at=when.date().isoformat(),
                system=p.system,
                understudy_before=p.understudy_before,
                understudy_after=p.understudy_after,
                knowers_before=p.knowers_before,
                knowers_after=p.knowers_after,
                prs_before=n_before,
                prs_after=n_after,
                hours_before=None if before is None else round(before, 2),
                hours_after=None if after is None else round(after, 2),
                project_ratio=round(project, 4),
                slowdown=None if reason else round(after / before / project, 4),
                reason=reason,
            )
        )
    return units


# --- statistics ----------------------------------------------------------------------


def ranks(xs: list[float]) -> list[float]:
    """1-based ranks, ties sharing the average of the ranks they span."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Pearson's correlation of the ranks; None when it is undefined (fewer than 3
    points, or every value on one side the same)."""
    if len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if not sxx or not syy:
        return None
    return sxy / math.sqrt(sxx * syy)


def interval(
    units: list[Unit], stat: Callable[[list[Unit]], float | None], seed: int = SEED
) -> tuple[float | None, float | None, int]:
    """95% percentile interval of `stat`, resampling whole departures with replacement.
    Returns (low, high, resamples where the statistic was defined)."""
    by_event = defaultdict(list)
    for u in units:
        by_event[u.person].append(u)
    keys = sorted(by_event)
    rng = random.Random(seed)
    values = []
    for _ in range(RESAMPLES):
        sample = [u for k in rng.choices(keys, k=len(keys)) for u in by_event[k]]
        v = stat(sample)
        if v is not None:
            values.append(v)
    if len(values) < RESAMPLES // 2:
        return None, None, len(values)  # too often undefined to mean anything
    values.sort()
    return values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1], len(values)


def _rho(field: str) -> Callable[[list[Unit]], float | None]:
    return lambda us: spearman([getattr(u, field) for u in us], [math.log(u.slowdown) for u in us])


def _median_of(group: Callable[[Unit], bool]) -> Callable[[list[Unit]], float | None]:
    def stat(us: list[Unit]) -> float | None:
        values = [u.slowdown for u in us if group(u)]
        return median(values) if values else None

    return stat


def summarise(units: list[Unit]) -> dict:
    used = [u for u in units if u.reason is None]

    def with_interval(stat):
        lo, hi, n = interval(used, stat)
        value = stat(used)
        return {
            "value": None if value is None else round(value, 3),
            "low": None if lo is None else round(lo, 3),
            "high": None if hi is None else round(hi, 3),
            "resamples": n,
        }

    def low(u: Unit) -> bool:
        return u.understudy_after <= LOW

    return {
        "departures": len({u.person for u in used}),
        "systems": len(used),
        "rho": {
            "understudy": with_interval(_rho("understudy_after")),
            "knowers": with_interval(_rho("knowers_after")),
        },
        "groups": {
            "low": {"n": sum(map(low, used)), **with_interval(_median_of(low))},
            "high": {
                "n": sum(not low(u) for u in used),
                **with_interval(_median_of(lambda u: not low(u))),
            },
        },
    }


# --- the whole run -------------------------------------------------------------------


def run(
    db: FalkorDB,
    raw: Path,
    prs: list[dict],
    aliases: list,
    deps: list[Departure],
    names: dict[str, str],
    salt: str,
    log: Callable[[str], None] = print,
) -> dict:
    first = min(_time(p["merged_at"]) for p in prs)
    last = max(_time(p["merged_at"]) for p in prs)
    evs = events(deps, prs, names, first, last)
    # Real logins stay in this process; only pseudonyms are written out.
    logins = {names[d.handle]: d.handle for d in deps if d.handle in names}
    prepared = prepare(prs)

    units: list[Unit] = []
    kept = [e for e in evs if e.reason is None]
    for day in sorted({e.left_at.date() for e in kept}):
        graph = f"{PREFIX}{day.isoformat()}"
        try:
            smap = snapshot(db, graph, prs, raw, aliases, day, salt)
            for e in (e for e in kept if e.left_at.date() == day):
                predictions = predict(db, graph, e.person)
                if not predictions:
                    e.reason = "knew and approved no system in the graph that day"
                    continue
                units += measure(e, predictions, prepared, smap.system_of, logins[e.person])
                log(f"{day} {e.person}: {len(predictions)} systems")
        finally:
            drop(db, graph)

    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data": {
            "prs": len(prs),
            "first_merge": first.date().isoformat(),
            "last_merge": last.date().isoformat(),
        },
        "method": {
            "window_days": WINDOW.days,
            "grace_days": GRACE.days,
            "min_prs": MIN_PRS,
            "min_active_prs": MIN_ACTIVE_PRS,
            "low_group": f"understudy number {LOW} or below after the departure",
            "resamples": RESAMPLES,
            "seed": SEED,
        },
        "summary": summarise(units),
        "events": [
            {**asdict(e), "left_at": e.left_at.date().isoformat()}
            for e in sorted(evs, key=lambda e: e.left_at)
        ],
        "units": [asdict(u) for u in units],
    }
