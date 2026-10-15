"""Fetch every kep.yaml from kubernetes/enhancements at the head of its default branch."""

from pathlib import Path

from .github import GitHub, log
from .owners import fetch_blobs


def fetch_keps(gh: GitHub, repo: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    branch = gh.rest(f"/repos/{repo}")["default_branch"]
    tree = gh.rest(f"/repos/{repo}/git/trees/{branch}", recursive=1)
    if tree["truncated"]:
        raise RuntimeError(f"{repo} tree was truncated; walk it level by level instead")
    paths = [
        e["path"]
        for e in tree["tree"]
        if e["type"] == "blob" and e["path"].startswith("keps/") and e["path"].endswith("/kep.yaml")
    ]
    log(f"KEPs: {len(paths)} kep.yaml files on {branch}")
    fetch_blobs(gh, repo, [(p, branch) for p in paths], out_dir / "keps.jsonl")
    log("KEPs done")
