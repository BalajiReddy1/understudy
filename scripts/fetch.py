"""Pull the raw Kubernetes data into data/raw/. Safe to stop and re-run: finished
pieces are cached on disk and skipped.

    uv run python scripts/fetch.py all
    uv run python scripts/fetch.py prs --start 2024-01-01 --end 2024-01-03   # quick check
    uv run python scripts/fetch.py owners --limit 10                         # quick check
"""

import argparse
from datetime import date
from pathlib import Path

from understudy.ingest.github import GitHub, log
from understudy.ingest.keps import fetch_keps
from understudy.ingest.owners import fetch_owners
from understudy.ingest.prs import fetch_prs

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
REPO = "kubernetes/kubernetes"
ENHANCEMENTS = "kubernetes/enhancements"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["prs", "owners", "keps", "all"])
    ap.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 14))
    ap.add_argument("--limit", type=int, help="only the first N OWNERS files")
    ap.add_argument("--out", type=Path, default=RAW)
    args = ap.parse_args()

    gh = GitHub()
    if args.what in ("keps", "all"):
        fetch_keps(gh, ENHANCEMENTS, args.out / "keps")
    if args.what in ("owners", "all"):
        fetch_owners(gh, REPO, f"{args.start}T00:00:00Z", args.out / "owners", args.limit)
    if args.what in ("prs", "all"):
        fetch_prs(gh, REPO, args.start, args.end, args.out / "prs")
    log("fetch finished")


if __name__ == "__main__":
    main()
