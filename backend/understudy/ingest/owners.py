"""Fetch the history of every OWNERS file (and OWNERS_ALIASES) in a repository.

Departures are people moved into emeritus_approvers. Rather than parsing commit
patches, this saves the full file text at every commit that touched it, so the
loader can diff the parsed YAML of consecutive versions. Both steps go through
GraphQL with many aliases per request, which costs a few hundred points in total.
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
    wanted = [(p, c["oid"]) for p, commits in history.items() for c in commits]
    log(f"OWNERS: {len(wanted)} file versions since {cutoff[:10]} (plus one baseline each)")

    fetch_blobs(gh, repo, wanted, out_dir / "blobs.jsonl")
    log("OWNERS done")
