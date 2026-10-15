"""Departures: approvers who left, read from OWNERS history.

Two kinds of event count, because Kubernetes records a departure in either place:
  - emeritus: a handle newly appears in emeritus_approvers in an OWNERS file.
  - roster:   a handle drops out of every alias in OWNERS_ALIASES and is not back by
              the latest version (so promotions and temporary removals don't count).

Every version of every OWNERS file since the window opened is on disk, so both are a
diff between consecutive versions. Moves often come months after someone stopped, so
each departure also carries the person's last activity before the move. That date,
not the commit date, is when the team actually lost them.
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import yaml

from .kubernetes import KNOWS_WINDOW, _participants, _time, load_aliases, load_versions


@dataclass
class Departure:
    handle: str  # real, lowercased; pseudonymise before anything leaves this process
    moved_at: datetime  # first event of either kind
    sources: list[str] = field(default_factory=list)  # OWNERS files the moves were in
    last_active: datetime | None = None  # last PR activity before the move
    prs_before: int = 0  # PRs in the 18 months before the move
    prs_after: int = 0  # PRs after the move (stepping back, not necessarily leaving)

    @property
    def left_at(self) -> datetime:
        return self.last_active or self.moved_at


def _emeritus(text: str | None) -> set[str] | None:
    """Emeritus approvers of one OWNERS version, or None if the version can't be read
    (deleted or broken), so a bad version never looks like everyone was just added."""
    if text is None:
        return None
    try:
        o = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(o, dict):
        return None
    names = list(o.get("emeritus_approvers") or [])
    for rule in (o.get("filters") or {}).values():
        names += (rule or {}).get("emeritus_approvers") or []
    return {n.lower() for n in names if isinstance(n, str)}


def emeritus_moves(raw: Path) -> list[tuple[str, datetime, str]]:
    """(handle, merge time, OWNERS path) for every handle newly added to an emeritus
    list. A file's oldest version is the baseline and yields nothing: it is either the
    state before the window or the commit that created the file."""
    history = load_versions(raw)
    texts = {}
    for line in (raw / "owners" / "blobs.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        texts[(r["path"], r["oid"])] = r["text"]

    moves = []
    for path, commits in history.items():
        if path.rsplit("/", 1)[-1] != "OWNERS":
            continue
        before = None
        for c in reversed(commits):  # oldest first
            now = _emeritus(texts.get((path, c["oid"])))
            if now is None:
                continue
            if before is not None:
                moves += [(h, _time(c["date"]), path) for h in sorted(now - before)]
            before = now
    return moves


def roster_exits(raw: Path) -> list[tuple[str, datetime, str]]:
    """(handle, merge time, 'OWNERS_ALIASES') for each person who drops out of every
    alias and is not in any alias in the latest version."""
    versions = load_aliases(raw)
    if not versions:
        return []

    def everyone(aliases: dict) -> set[str]:
        return {h.lower() for hs in aliases.values() for h in hs or [] if isinstance(h, str)}

    final = everyone(versions[-1][1])
    exits = {}
    for (_, before), (when, after) in pairwise(versions):
        for h in everyone(before) - everyone(after) - final:
            exits.setdefault(h, when)  # the first time they dropped out
    return [(h, when, "OWNERS_ALIASES") for h, when in sorted(exits.items())]


def departures(raw: Path, prs: list[dict]) -> list[Departure]:
    by_person: dict[str, Departure] = {}
    events = emeritus_moves(raw) + roster_exits(raw)
    for handle, when, source in sorted(events, key=lambda m: m[1]):
        dep = by_person.setdefault(handle, Departure(handle, when))
        if source not in dep.sources:
            dep.sources.append(source)

    activity = defaultdict(list)
    for pr in prs:
        merged = _time(pr["merged_at"])
        for person in _participants(pr):
            if person in by_person:
                activity[person].append(merged)

    for dep in by_person.values():
        times = activity[dep.handle]
        dep.last_active = max((t for t in times if t <= dep.moved_at), default=None)
        dep.prs_before = sum(dep.moved_at - KNOWS_WINDOW < t <= dep.moved_at for t in times)
        dep.prs_after = sum(t > dep.moved_at for t in times)
    return sorted(by_person.values(), key=lambda d: d.moved_at)
