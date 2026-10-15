"""Turn the raw Kubernetes pull into an org (the dict shape flow.write_org takes).

    systems       OWNERS directories 1 to 3 levels under pkg/, cmd/, plugin/ and
                  staging/src/k8s.io/. A directory with too few PRs folds into the
                  nearest OWNERS directory above it.
    teams         The approvers of each system, read from its nearest OWNERS file the
                  way Kubernetes' approval bot does: aliases expanded and the catch-all
                  `.*` filter included. If everyone a file lists has gone quiet,
                  approval falls to the next file up (as it does in practice) and the
                  system records which file was abandoned. Systems sharing an OWNERS
                  file share a team.
    MEMBER_OF     approvers with at least 3 PRs in the knows window. Anyone else
                  listed has effectively left already.
    KNOWS         PRs touching the system in the 18 months before the as-of date:
                  author 1.0, reviewer or commenter 0.5, halving every 6 months.
    COLLABORATES  one reviewed or commented on the other's PRs at least twice in 12 months.

The SIG that owns each system is kept as context (System.sigs), not as the team. Two
earlier versions used SIGs as teams. With membership by activity, nearly every knower
was on the team and the understudy number repeated the knower count. With the SIG
rosters in OWNERS_ALIASES, most systems scored the roster size. The approvers of the
code are the people who actually have to be reached, so the question becomes: how
many people can leave before no approver can reach anyone who knows this code?
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
# An approver with fewer PRs than this in the knows window is treated as gone. One
# drive-by comment in 18 months is not someone a team can rely on.
MIN_ACTIVE_PRS = 3
# PRs touching more systems than this are sweeps (renames, dependency bumps, lint
# fixes). They say little about who understands any one of them.
MAX_SYSTEMS_PER_PR = 8


def _time(s: str) -> datetime:
    return datetime.fromisoformat(s)


def end_of(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=UTC)


def _dir(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _parse(text: str) -> dict:
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_prs(raw: Path) -> list[dict]:
    by_number = {}
    for f in sorted((raw / "prs").glob("*.jsonl")):
        for line in f.open(encoding="utf-8"):
            r = json.loads(line)
            by_number[r["number"]] = r
    return list(by_number.values())


def load_versions(raw: Path) -> dict[str, list[dict]]:
    """path -> [{oid, date}] newest first: each OWNERS file as it landed on the main
    branch, dated by merge time (see ingest/owners.py for why not branch commits)."""
    return json.loads((raw / "owners" / "mainline.json").read_text())


def load_owners(raw: Path, as_of: date | None = None) -> dict[str, dict]:
    """Each OWNERS file as it stood at the end of `as_of` (default: latest), keyed by
    directory ('' is the repo root). Files deleted by then, or not yet created, are
    left out. Only files that still exist today were fetched, so a directory removed
    before today is missing from older dates too."""
    until = end_of(as_of) if as_of else None
    wanted = set()
    for path, commits in load_versions(raw).items():
        if path.rsplit("/", 1)[-1] != "OWNERS":
            continue
        for c in commits:  # newest first
            if until is None or _time(c["date"]) <= until:
                wanted.add((path, c["oid"]))
                break
    out = {}
    for line in (raw / "owners" / "blobs.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        # text is None when that commit deleted the file.
        if (r["path"], r["oid"]) in wanted and r["text"] is not None:
            out[_dir(r["path"])] = _parse(r["text"])
    return out


def load_aliases(raw: Path) -> list[tuple[datetime, dict[str, list[str]]]]:
    """Every version of OWNERS_ALIASES as (merge time, aliases), oldest first."""
    when = {c["oid"]: _time(c["date"]) for c in load_versions(raw).get("OWNERS_ALIASES", [])}
    versions = []
    for line in (raw / "owners" / "blobs.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r["path"] == "OWNERS_ALIASES" and r["text"] and r["oid"] in when:
            versions.append((when[r["oid"]], _parse(r["text"]).get("aliases") or {}))
    return sorted(versions, key=lambda v: v[0])


def aliases_at(versions: list[tuple[datetime, dict]], until: datetime) -> dict[str, list[str]]:
    current = [a for when, a in versions if when <= until]
    return current[-1] if current else {}


def team_name(owner_dir: str) -> str:
    """A team is named after the OWNERS file its approvers come from."""
    return f"{owner_dir}/OWNERS" if owner_dir else "OWNERS"


def _sig_labels(labels) -> list[str]:
    return sorted(
        {lab.replace("sig/", "sig-", 1) for lab in labels or [] if lab.startswith("sig/")}
    )


class OwnersTree:
    """Reads approvers and SIG labels for a directory from a set of OWNERS files."""

    def __init__(self, files: dict[str, dict], aliases: dict[str, list[str]]):
        self.files = files
        self.aliases = aliases

    def _level(self, d: str) -> tuple[list[str], list[str], list[str], bool]:
        """(approvers, labels, emeritus approvers, no_parent_owners) of one file. Rules
        under `filters` apply to matching file names; only the catch-all `.*` rule
        covers the whole directory, so it is the only one read here."""
        o = self.files.get(d) or {}
        catch_all = (o.get("filters") or {}).get(".*") or {}
        return (
            list(o.get("approvers") or []) + list(catch_all.get("approvers") or []),
            list(o.get("labels") or []) + list(catch_all.get("labels") or []),
            list(o.get("emeritus_approvers") or [])
            + list(catch_all.get("emeritus_approvers") or []),
            bool((o.get("options") or {}).get("no_parent_owners")),
        )

    def _up(self, d: str):
        """d and each directory above it that has an OWNERS file, nearest first."""
        while True:
            if d in self.files:
                yield d
            if not d:
                return
            d = _dir(d)

    def resolve(self, names: list[str]) -> set[str]:
        out = set()
        for n in names:
            if n in self.aliases:
                out |= {h.lower() for h in self.aliases[n] or []}
            else:
                out.add(n.lower())
        return out

    def approvers(self, d: str, active: set[str]) -> tuple[str | None, set[str], str | None]:
        """Who can approve changes to d today, as (directory of the OWNERS file they
        come from, their active lowercased handles, directory of the nearest OWNERS
        file whose listed approvers are all inactive, if approval had to move past it).

        Approvers in any parent file can approve too, unless a file sets
        no_parent_owners. So when everyone a file lists has gone quiet, approval falls
        to the next file up that still has someone active."""
        abandoned = None
        for x in self._up(d):
            listed, _, _, no_parent = self._level(x)
            here = self.resolve(listed) & active
            if here or no_parent:
                return x, here, abandoned
            if listed and abandoned is None:
                abandoned = x
        return None, set(), abandoned

    def sigs(self, d: str) -> list[str]:
        for x in self._up(d):
            labels = _sig_labels(self._level(x)[1])
            if labels:
                return labels
        return []

    def emeritus(self, d: str) -> int:
        """Former approvers recorded in this directory's own OWNERS file."""
        return len(self.resolve(self._level(d)[2])) if d in self.files else 0


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
    """Returns the org and the login -> pseudonym map. Pass OWNERS files loaded for
    the same as-of date. The map is for matching departures later; it must never be
    written anywhere public."""
    until = end_of(as_of)
    knows_since, recent_since = until - KNOWS_WINDOW, until - RECENT_WINDOW
    smap = SystemMap(owners, prs, knows_since, until)
    tree = OwnersTree(owners, aliases_at(aliases, until))

    knows: dict[tuple[str, str], float] = defaultdict(float)
    collab: Counter = Counter()
    pr_sigs: dict[str, Counter] = defaultdict(Counter)
    activity: Counter = Counter()

    for pr in prs:
        merged = _time(pr["merged_at"])
        if not knows_since < merged <= until:
            continue
        people = _participants(pr)
        activity.update(people.keys())
        systems = {s for f in pr["files"] if (s := smap.system_of(f))}
        for s in systems:
            pr_sigs[s].update(_sig_labels(pr["labels"]))
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

    active = {p for p, n in activity.items() if n >= MIN_ACTIVE_PRS}
    systems, approvers_of = [], {}
    for s in smap.systems:
        owner_dir, approvers, abandoned = tree.approvers(s, active)
        if owner_dir is None:
            continue
        team = team_name(owner_dir)
        approvers_of[team] = approvers
        systems.append(
            {
                "name": s,
                "teams": [team],
                "sigs": tree.sigs(s) or [sig for sig, _ in pr_sigs[s].most_common(1)],
                "emeritus_approvers": tree.emeritus(s),
                # The OWNERS file whose approvers have all gone quiet, if approval
                # had to move up past it.
                "inactive_owners": abandoned and team_name(abandoned),
            }
        )
    system_names = {s["name"] for s in systems}

    knows_rows = [
        (p, s, round(v, 3)) for (p, s), v in knows.items() if v >= MIN_KNOWS and s in system_names
    ]
    member_rows = [(p, t) for t, handles in approvers_of.items() for p in handles]
    collab_rows = [pair for pair, n in collab.items() if n >= MIN_COLLAB]

    involved = {p for p, _, _ in knows_rows} | {p for p, _ in member_rows}
    # A collaborator outside both sets can't start or end a route, but can sit in the
    # middle of one, so they stay in.
    involved |= {p for pair in collab_rows for p in pair}

    names = pseudonyms(all_logins(prs), salt)
    teams_of = defaultdict(list)
    for p, t in member_rows:
        teams_of[p].append(t)

    org = {
        "teams": [{"name": t, "kind": "approvers"} for t in sorted(approvers_of)],
        "systems": systems,
        "people": [{"handle": names[p], "teams": sorted(teams_of[p])} for p in sorted(involved)],
        "knows": [{"person": names[p], "system": s, "score": v} for p, s, v in knows_rows],
        "collaborates": [[names[a], names[b]] for a, b in collab_rows],
    }
    return org, names
