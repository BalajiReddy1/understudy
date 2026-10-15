"""Fetch merged pull requests with their files, reviews and comments.

GitHub search returns at most 1,000 results per query, so the window is walked one
calendar month at a time and a month is split in half whenever it holds more than that.
Each finished window is written to its own file, so a stopped pull resumes where it was.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

from .github import GitHub, log

PR_FIELDS = """
number title createdAt mergedAt body changedFiles
author { login __typename }
labels(first: 30) { nodes { name } }
files(first: 100) { nodes { path } }
reviews(first: 30) { nodes { author { login __typename } createdAt state } }
comments(first: 60) { nodes { author { login __typename } createdAt } }
"""

SEARCH = """
query($q: String!, $first: Int!, $after: String) {
  search(type: ISSUE, query: $q, first: $first, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { PR_FIELDS } }
  }
  rateLimit { remaining resetAt }
}
""".replace("PR_FIELDS", PR_FIELDS)

# KEP mentions in a PR body: "KEP-1287", "KEP 1287", or links into kubernetes/enhancements.
KEP_RE = re.compile(
    r"KEP[-\s#]?(\d{1,5})\b"
    r"|kubernetes/enhancements/(?:issues|pull)/(\d{1,5})"
    r"|keps/sig-[\w-]+/(\d{1,5})-",
    re.IGNORECASE,
)


def kep_refs(body: str | None) -> list[int]:
    found = set()
    for m in KEP_RE.finditer(body or ""):
        found.add(int(next(g for g in m.groups() if g)))
    return sorted(found)


def who(actor: dict | None) -> tuple[str | None, bool]:
    if not actor:
        return None, False  # deleted account ("ghost")
    return actor["login"], actor["__typename"] == "Bot"


def slim(pr: dict) -> dict:
    author, author_bot = who(pr["author"])
    return {
        "number": pr["number"],
        "title": pr["title"],
        "created_at": pr["createdAt"],
        "merged_at": pr["mergedAt"],
        "author": author,
        "author_is_bot": author_bot,
        "labels": [n["name"] for n in pr["labels"]["nodes"]],
        "changed_files": pr["changedFiles"],
        "files": [n["path"] for n in pr["files"]["nodes"]],
        "reviews": [
            {
                "author": who(n["author"])[0],
                "bot": who(n["author"])[1],
                "at": n["createdAt"],
                "state": n["state"],
            }
            for n in pr["reviews"]["nodes"]
        ],
        "comments": [
            {"author": who(n["author"])[0], "bot": who(n["author"])[1], "at": n["createdAt"]}
            for n in pr["comments"]["nodes"]
        ],
        "kep_refs": kep_refs(pr["body"]),
    }


def months(start: date, end: date):
    cur = start.replace(day=1)
    while cur <= end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield max(cur, start), min(nxt - timedelta(days=1), end)
        cur = nxt


def fetch_window(gh: GitHub, repo: str, lo: date, hi: date, out_dir: Path) -> int:
    out = out_dir / f"{lo}_{hi}.jsonl"
    if out.exists():
        return sum(1 for _ in out.open(encoding="utf-8"))

    q = f"repo:{repo} is:pr is:merged merged:{lo}..{hi}"
    page_size, cursor, rows = 25, None, []
    while True:
        try:
            data = gh.graphql(SEARCH, {"q": q, "first": page_size, "after": cursor})
        except RuntimeError:
            if page_size == 5:
                raise
            page_size = max(5, page_size // 2)
            log(f"  heavy page, dropping page size to {page_size}")
            continue
        s = data["search"]
        if cursor is None and s["issueCount"] > 1000:
            if lo == hi:
                raise RuntimeError(f"more than 1,000 PRs merged on {lo}; cannot split further")
            mid = lo + (hi - lo) // 2
            log(f"  {lo}..{hi} has {s['issueCount']} PRs, splitting")
            return fetch_window(gh, repo, lo, mid, out_dir) + fetch_window(
                gh, repo, mid + timedelta(days=1), hi, out_dir
            )
        rows += [slim(n) for n in s["nodes"] if n]
        if not s["pageInfo"]["hasNextPage"]:
            break
        cursor = s["pageInfo"]["endCursor"]

    tmp = out.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    tmp.replace(out)
    log(f"  {lo}..{hi}: {len(rows)} PRs (rate limit left {data['rateLimit']['remaining']})")
    return len(rows)


def fetch_prs(gh: GitHub, repo: str, start: date, end: date, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for lo, hi in months(start, end):
        total += fetch_window(gh, repo, lo, hi, out_dir)
    log(f"PRs done: {total} merged PRs from {start} to {end}")
