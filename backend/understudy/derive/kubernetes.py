"""Turn the raw Kubernetes pull into an org (the dict shape flow.write_org takes).

    systems       OWNERS directories 1 to 3 levels under pkg/, cmd/, plugin/ and
                  staging/src/k8s.io/. A directory with too few PRs folds into the
                  nearest OWNERS directory above it.
    teams         SIGs. A system's owners come from `labels: sig/...` in its OWNERS
                  file, else the nearest OWNERS above it, else the commonest sig label
                  on PRs that touch it.
    KNOWS         PRs touching the system in the 18 months before the as-of date:
                  author 1.0, reviewer or commenter 0.5, halving every 6 months.
    COLLABORATES  one reviewed or commented on the other's PRs at least twice in 12 months.
    MEMBER_OF     the SIG's roster in OWNERS_ALIASES as of that date (sig-node-approvers,
                  sig-node-reviewers, ...), for people active in the knows window. A SIG
                  with no roster falls back to anyone with 3+ sig/X PRs in 12 months.

Membership was activity-based at first, but then nearly every knower was already on
the team and the understudy number just repeated the knower count. With the official
roster, the question becomes the one the product asks: how many people can leave
before none of the SIG's maintainers can reach someone who knows the code.
"""

import json
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml

from .people import is_bot, pseudonyms

ROOTS = ("pkg/", "cmd/", "plugin/", "staging/src/k8s.io/")
MAX_DEPTH = 3
# Shared registries and generated code: every feature PR touches them, nobody owns them.
EXCLUDE = {"pkg/features", "pkg/generated", "pkg/generated/openapi"}
MIN_PRS = 20  # PRs in the knows window a directory needs to stand as its own system

KNOWS_WINDOW = timedelta(days=548)
RECENT_WINDOW = timedelta(days=365)
HALF_LIFE_DAYS = 182.6
AUTHOR, REVIEWER = 1.0, 0.5
MIN_KNOWS = 1.0
MIN_COLLAB = 2
MIN_MEMBER = 3
# PRs touching more systems than this are sweeps (renames, dependency bumps, lint
# fixes). They say little about who understands any one of them.
MAX_SYSTEMS_PER_PR = 8


def _time(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _dir(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def load_prs(raw: Path) -> list[dict]:
    by_number = {}
    for f in sorted((raw / "prs").glob("*.jsonl")):
        for line in f.open(encoding="utf-8"):
            r = json.loads(line)
            by_number[r["number"]] = r
    return list(by_number.values())


def load_owners(raw: Path) -> dict[str, dict]:
    """Latest parsed OWNERS file for each directory ('' is the repo root)."""
    history = json.loads((raw / "owners" / "history.json").read_text())
    latest = {(p, c[0]["oid"]) for p, c in history.items() if c and p.endswith("OWNERS")}
    out = {}
    for line in (raw / "owners" / "blobs.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if (r["path"], r["oid"]) in latest and r["text"]:
            try:
                out[_dir(r["path"])] = yaml.safe_load(r["text"]) or {}
            except yaml.YAMLError:
                out[_dir(r["path"])] = {}
    return out


def load_aliases(raw: Path) -> list[tuple[datetime, dict[str, list[str]]]]:
    """Every version of OWNERS_ALIASES as (commit time, aliases), oldest first."""
    history = json.loads((raw / "owners" / "history.json").read_text())
    when = {c["oid"]: _time(c["date"]) for c in history.get("OWNERS_ALIASES", [])}
    versions = []
    for line in (raw / "owners" / "blobs.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r["path"] == "OWNERS_ALIASES" and r["text"] and r["oid"] in when:
            aliases = (yaml.safe_load(r["text"]) or {}).get("aliases") or {}
            versions.append((when[r["oid"]], aliases))
    return sorted(versions, key=lambda v: v[0])


def rosters(aliases: dict[str, list[str]], sigs: set[str]) -> dict[str, set[str]]:
    """SIG -> lowercased handles on any alias named after it (sig-auth-audit-approvers
    belongs to sig-auth). The longest matching SIG name wins."""
    out: dict[str, set[str]] = defaultdict(set)
    for name, handles in aliases.items():
        owner = max(
            (s for s in sigs if name == s or name.startswith(s + "-")), key=len, default=None
        )
        if owner:
            out[owner] |= {h.lower() for h in handles or []}
    return out


def _sig_labels(labels) -> list[str]:
    return sorted(
        {lab.replace("sig/", "sig-", 1) for lab in labels or [] if lab.startswith("sig/")}
    )


def _participants(pr: dict) -> dict[str, float]:
    """Each non-bot person on a PR with their strongest role, handles lowercased."""
    people: dict[str, float] = {}
    if not is_bot(pr["author"], pr["author_is_bot"]):
        people[pr["author"].lower()] = AUTHOR
    for x in pr["reviews"] + pr["comments"]:
        if not is_bot(x["author"], x["bot"]):
            people.setdefault(x["author"].lower(), REVIEWER)
    return people


def all_logins(prs: list[dict]) -> set[str]:
    return {p for pr in prs for p in _participants(pr)}


class SystemMap:
    """Maps a file path to the system that owns it."""

    def __init__(self, owners: dict[str, dict], prs: list[dict], since: datetime, until: datetime):
        self.candidates = set()
        for d in owners:
            if d in EXCLUDE:
                continue
            for root in ROOTS:
                if d.startswith(root) and d.count("/") - root.count("/") + 1 <= MAX_DEPTH:
                    self.candidates.add(d)
        self.final: dict[str, str | None] = {d: d for d in self.candidates}

        # Sweeps are left out of the count too. Dependency bumps touch the go.mod at the
        # root of every staging repo, which would otherwise keep each root alive as a
        # system that nobody actually works in.
        touched = defaultdict(set)
        for pr in prs:
            if since < _time(pr["merged_at"]) <= until:
                hit = {c for f in pr["files"] if (c := self._candidate(_dir(f)))}
                if len(hit) <= MAX_SYSTEMS_PER_PR:
                    for c in hit:
                        touched[c].add(pr["number"])
        # Deepest first, so a thin directory's PRs count toward its parent's total.
        for d in sorted(self.candidates, key=lambda d: -d.count("/")):
            if len(touched[d]) < MIN_PRS:
                parent = self._candidate(_dir(d))
                self.final[d] = parent
                if parent:
                    touched[parent] |= touched[d]
        self.systems = sorted(d for d in self.candidates if self.final[d] == d)

    def _candidate(self, d: str) -> str | None:
        while d:
            if d in self.candidates:
                return d
            d = _dir(d)
        return None

    def system_of(self, path: str) -> str | None:
        d = self._candidate(_dir(path))
        while d is not None and self.final[d] != d:
            d = self.final[d]
        return d


def derive_org(
    prs: list[dict],
    owners: dict[str, dict],
    aliases: list[tuple[datetime, dict]],
    as_of: date,
    salt: str,
) -> tuple[dict, dict[str, str]]:
    """Returns the org and the login -> pseudonym map. The map is for matching
    departures later; it must never be written anywhere public."""
    until = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=UTC)
    knows_since, recent_since = until - KNOWS_WINDOW, until - RECENT_WINDOW
    smap = SystemMap(owners, prs, knows_since, until)

    knows: dict[tuple[str, str], float] = defaultdict(float)
    collab: Counter = Counter()
    member: Counter = Counter()
    pr_sigs: dict[str, Counter] = defaultdict(Counter)
    active: set[str] = set()

    for pr in prs:
        merged = _time(pr["merged_at"])
        if not knows_since < merged <= until:
            continue
        people = _participants(pr)
        active |= people.keys()
        systems = {s for f in pr["files"] if (s := smap.system_of(f))}
        sigs = _sig_labels(pr["labels"])
        for s in systems:
            pr_sigs[s].update(sigs)
        if len(systems) <= MAX_SYSTEMS_PER_PR:
            decay = 0.5 ** ((until - merged).days / HALF_LIFE_DAYS)
            for person, weight in people.items():
                for s in systems:
                    knows[(person, s)] += weight * decay
        if merged > recent_since:
            author = pr["author"] and pr["author"].lower()
            if author in people:
                for other in people:
                    if other != author:
                        collab[tuple(sorted((author, other)))] += 1
            for person in people:
                for sig in sigs:
                    member[(person, sig)] += 1

    systems = []
    for s in smap.systems:
        teams = None
        d = s
        while teams is None and d is not None:
            if d in owners:
                teams = _sig_labels(owners[d].get("labels")) or None
            d = _dir(d) if d else None
        if teams is None and pr_sigs[s]:
            teams = [pr_sigs[s].most_common(1)[0][0]]
        if teams:
            systems.append({"name": s, "teams": teams})
    system_names = {s["name"] for s in systems}

    knows_rows = [
        (p, s, round(v, 3)) for (p, s), v in knows.items() if v >= MIN_KNOWS and s in system_names
    ]
    sigs_seen = {t for s in systems for t in s["teams"]} | {sig for _, sig in member}
    current = [a for when, a in aliases if when <= until]
    roster = rosters(current[-1] if current else {}, sigs_seen)
    member_rows = [(p, sig) for sig, handles in roster.items() for p in handles & active]
    member_rows += [
        (p, sig) for (p, sig), n in member.items() if n >= MIN_MEMBER and sig not in roster
    ]
    collab_rows = [pair for pair, n in collab.items() if n >= MIN_COLLAB]

    involved = {p for p, _, _ in knows_rows} | {p for p, _ in member_rows}
    # A collaborator outside both sets can't start or end a route, but can sit in the
    # middle of one, so they stay in.
    involved |= {p for pair in collab_rows for p in pair}

    names = pseudonyms(all_logins(prs), salt)
    teams_of = defaultdict(list)
    for p, sig in member_rows:
        teams_of[p].append(sig)
    all_teams = sorted({t for s in systems for t in s["teams"]} | {sig for _, sig in member_rows})

    org = {
        "teams": all_teams,
        "systems": systems,
        "people": [{"handle": names[p], "teams": sorted(teams_of[p])} for p in sorted(involved)],
        "knows": [{"person": names[p], "system": s, "score": v} for p, s, v in knows_rows],
        "collaborates": [[names[a], names[b]] for a, b in collab_rows],
    }
    return org, names
