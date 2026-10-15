"""Fetch the history of every OWNERS file (and OWNERS_ALIASES) in a repository.

Departures are people moved into emeritus_approvers. Rather than parsing commit
patches, this saves the full file text as it stood on the main branch after every
change, so the loader can diff the parsed YAML of consecutive versions. Everything
goes through GraphQL with many aliases per request, a few hundred points in total.

    history.json   commits touching each file (PR branch commits, branch dates)
    landed.json    commit -> the merge commit that brought it into the main branch
    mainline.json  each file's versions on the main branch, newest first, by merge time
    blobs.jsonl    file text for every (path, oid) above
"""

import json
from pathlib import Path

from .github import GitHub, log

SKIP_DIRS = {"vendor", "third_party", "_output"}
NAMES = {"OWNERS", "OWNERS_ALIASES"}


def list_owners_files(gh: GitHub, repo: str, branch: str) -> list[str]:
    found: list[str] = []

    def walk(sha: str, prefix: str) -> None:
        t = gh.rest(f"/repos/{repo}/git/trees/{sha}", recursive=1)
        if not t["truncated"]:
            for e in t["tree"]:
                parts = e["path"].split("/")
                if e["type"] == "blob" and parts[-1] in NAMES and not SKIP_DIRS & set(parts):
                    found.append(prefix + e["path"])
            return
        # Too big for one recursive call: list this level, then descend into each subtree.
        for e in gh.rest(f"/repos/{repo}/git/trees/{sha}")["tree"]:
            if e["type"] == "blob" and e["path"] in NAMES:
                found.append(prefix + e["path"])
            elif e["type"] == "tree" and e["path"] not in SKIP_DIRS:
                walk(e["sha"], f"{prefix}{e['path']}/")

    walk(branch, "")
    return sorted(found)


def _history_query(owner: str, name: str, branch: str, paths: list[str], after: str | None) -> str:
    cursor = f", after: {json.dumps(after)}" if after else ""
    parts = [
        f"p{i}: object(expression: {json.dumps(branch)}) {{ ... on Commit {{ "
        f"history(path: {json.dumps(p)}, first: 100{cursor}) {{ "
        f"pageInfo {{ hasNextPage endCursor }} nodes {{ oid committedDate }} }} }} }}"
        for i, p in enumerate(paths)
    ]
    return f'query {{ repository(owner: "{owner}", name: "{name}") {{ {" ".join(parts)} }} }}'


def fetch_history(gh: GitHub, repo: str, branch: str, paths: list[str], cutoff: str) -> dict:
    """Commits touching each path, newest first, back to and including the first
    commit before `cutoff` (that one is the baseline version for diffing)."""
    owner, name = repo.split("/")
    history: dict[str, list[dict]] = {}
    for start in range(0, len(paths), 20):
        batch = paths[start : start + 20]
        data = gh.graphql(_history_query(owner, name, branch, batch, None))["repository"]
        for i, path in enumerate(batch):
            h = data[f"p{i}"]["history"]
            nodes = h["nodes"]
            # Rarely a file has more than 100 commits since the cutoff; page on for those.
            while h["pageInfo"]["hasNextPage"] and nodes[-1]["committedDate"] >= cutoff:
                more = gh.graphql(
                    _history_query(owner, name, branch, [path], h["pageInfo"]["endCursor"])
                )
                h = more["repository"]["p0"]["history"]
                nodes += h["nodes"]
            kept = []
            for n in nodes:
                kept.append({"oid": n["oid"], "date": n["committedDate"]})
                if n["committedDate"] < cutoff:
                    break
            history[path] = kept
        log(f"  history: {min(start + 20, len(paths))}/{len(paths)} files")
    return history


def fetch_blobs(gh: GitHub, repo: str, wanted: list[tuple[str, str]], out: Path) -> None:
    """Append {path, oid, text} for each (path, oid) not already in `out`."""
    owner, name = repo.split("/")
    done = set()
    if out.exists():
        for line in out.open(encoding="utf-8"):
            r = json.loads(line)
            done.add((r["path"], r["oid"]))
    todo = [w for w in wanted if w not in done]
    log(f"  blobs: {len(todo)} to fetch, {len(done)} cached")
    with out.open("a", encoding="utf-8") as f:
        for start in range(0, len(todo), 40):
            batch = todo[start : start + 40]
            parts = [
                f"b{i}: object(expression: {json.dumps(f'{oid}:{path}')}) {{ ... on Blob {{ text }} }}"
                for i, (path, oid) in enumerate(batch)
            ]
            q = f'query {{ repository(owner: "{owner}", name: "{name}") {{ {" ".join(parts)} }} }}'
            data = gh.graphql(q)["repository"]
            for i, (path, oid) in enumerate(batch):
                blob = data.get(f"b{i}")
                # None means the commit deleted the file at that path.
                f.write(
                    json.dumps({"path": path, "oid": oid, "text": blob and blob["text"]}) + "\n"
                )
            f.flush()
            if (start // 40) % 10 == 0:
                log(f"  blobs: {min(start + 40, len(todo))}/{len(todo)}")


def mainline_versions(
    history: dict[str, list[dict]], landed: dict[str, tuple[str, str] | None]
) -> dict[str, list[dict]]:
    """Each file's versions as they landed on the main branch, newest first.

    history(path) lists the commits on PR branches, with branch commit dates. Two PRs
    editing the same file at once each start from an older base, so diffing those
    versions in date order makes one PR look like it reverts the other. Mapping every
    commit to the merge commit that brought it in, and reading the file there, gives
    the file exactly as the main branch had it, in the order the changes arrived.
    `landed` maps commit oid -> (merge commit oid, merged at), or None if no merged PR
    contains it; such a commit was pushed directly and counts as landing when made."""
    out = {}
    for path, commits in history.items():
        versions = {}
        for c in commits:
            oid, when = landed.get(c["oid"]) or (c["oid"], c["date"])
            versions[oid] = min(when, versions.get(oid, when))
        out[path] = [
            {"oid": oid, "date": when}
            for oid, when in sorted(versions.items(), key=lambda v: v[1], reverse=True)
        ]
    return out


def fetch_landed(gh: GitHub, repo: str, branch: str, oids: list[str], out: Path) -> dict:
    """commit oid -> (merge commit oid, merged at) of the PR that brought it into
    `branch`, cached in `out`. Cherry-pick PRs into release branches are ignored."""
    owner, name = repo.split("/")
    landed = json.loads(out.read_text()) if out.exists() else {}
    todo = [o for o in oids if o not in landed]
    log(f"  landing points: {len(todo)} commits to look up, {len(landed)} cached")
    for start in range(0, len(todo), 40):
        batch = todo[start : start + 40]
        parts = [
            f'c{i}: object(oid: "{oid}") {{ ... on Commit {{ associatedPullRequests(first: 10) '
            "{ nodes { baseRefName mergedAt mergeCommit { oid } } } } }"
            for i, oid in enumerate(batch)
        ]
        q = f'query {{ repository(owner: "{owner}", name: "{name}") {{ {" ".join(parts)} }} }}'
        data = gh.graphql(q)["repository"]
        for i, oid in enumerate(batch):
            prs = [
                (p["mergeCommit"]["oid"], p["mergedAt"])
                for p in ((data.get(f"c{i}") or {}).get("associatedPullRequests") or {}).get(
                    "nodes", []
                )
                if p["baseRefName"] == branch and p["mergedAt"] and p["mergeCommit"]
            ]
            landed[oid] = min(prs, key=lambda p: p[1]) if prs else None
        out.write_text(json.dumps(landed))
    return landed


def fetch_owners(
    gh: GitHub, repo: str, cutoff: str, out_dir: Path, limit: int | None = None
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    branch = gh.rest(f"/repos/{repo}")["default_branch"]

    files_path = out_dir / "files.json"
    if files_path.exists():
        paths = json.loads(files_path.read_text())
    else:
        paths = list_owners_files(gh, repo, branch)
        files_path.write_text(json.dumps(paths, indent=1))
    log(f"OWNERS: {len(paths)} files on {branch}")
    if limit:
        paths = paths[:limit]

    hist_path = out_dir / ("history.json" if not limit else "history.sample.json")
    if hist_path.exists():
        history = json.loads(hist_path.read_text())
    else:
        history = fetch_history(gh, repo, branch, paths, cutoff)
        hist_path.write_text(json.dumps(history))
    log(
        f"OWNERS: {sum(map(len, history.values()))} commits since {cutoff[:10]} "
        "(plus one baseline each)"
    )

    oids = sorted({c["oid"] for commits in history.values() for c in commits})
    landed = fetch_landed(gh, repo, branch, oids, out_dir / "landed.json")
    mainline = mainline_versions(history, {o: landed[o] and tuple(landed[o]) for o in oids})
    (out_dir / ("mainline.json" if not limit else "mainline.sample.json")).write_text(
        json.dumps(mainline)
    )
    wanted = [(p, v["oid"]) for p, versions in mainline.items() for v in versions]
    log(f"OWNERS: {len(wanted)} versions as they landed on {branch}")

    fetch_blobs(gh, repo, wanted, out_dir / "blobs.jsonl")
    log("OWNERS done")
