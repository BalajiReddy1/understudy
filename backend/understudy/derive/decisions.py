"""Decisions: the KEPs (Kubernetes Enhancement Proposals) behind each system.

A KEP is linked to a system when merged PRs that touch the system name it (KEP-1287,
or a link into kubernetes/enhancements), weighted by how many PRs do. Its approvers
are pseudonymised like everyone else, so `why` can say whether the people who signed
off on a design are still around."""

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

ENHANCEMENTS = "https://github.com/kubernetes/enhancements"


def _handles(values) -> list[str]:
    out = []
    for v in values or []:
        if isinstance(v, str) and v.strip():
            out.append(v.strip().lstrip("@").lower())
    return out


def load_keps(raw: Path) -> dict[int, dict]:
    keps = {}
    for line in (raw / "keps" / "keps.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        try:
            y = yaml.safe_load(r["text"] or "") or {}
        except (yaml.YAMLError, ValueError):
            # ValueError: a few real kep.yaml files carry impossible dates (month 13),
            # which PyYAML rejects while building the timestamp, not while parsing.
            continue
        n = y.get("kep-number") if isinstance(y, dict) else None
        if isinstance(n, str) and n.isdigit():
            n = int(n)
        if not isinstance(n, int):
            continue  # the template and a few drafts have no number
        keps[n] = {
            "number": n,
            "title": str(y.get("title") or "").strip(),
            "status": y.get("status"),
            "stage": y.get("stage"),
            "sig": y.get("owning-sig"),
            "url": f"{ENHANCEMENTS}/tree/master/{r['path'].rsplit('/', 1)[0]}",
            "approvers": _handles(y.get("approvers")),
        }
    return keps


def link_decisions(
    prs: list[dict],
    system_of,
    keps: dict[int, dict],
    names: dict[str, str],
    until: datetime,
) -> list[dict]:
    """Decisions for the org dict, with the systems each one shaped and its approvers
    as pseudonyms. Approvers who never appear in the PR data can't be named, so only
    their count is kept."""
    shaped: dict[int, Counter] = defaultdict(Counter)
    for pr in prs:
        if not pr["kep_refs"] or datetime.fromisoformat(pr["merged_at"]) > until:
            continue
        systems = {s for f in pr["files"] if (s := system_of(f))}
        for n in pr["kep_refs"]:
            if n in keps:
                shaped[n].update(systems)
    decisions = []
    for n, systems in sorted(shaped.items()):
        if not systems:
            continue
        k = keps[n]
        decisions.append(
            {
                **{f: k[f] for f in ("number", "title", "status", "stage", "sig", "url")},
                "approvers": sorted(names[a] for a in k["approvers"] if a in names),
                "approvers_total": len(k["approvers"]),
                "systems": [{"name": s, "prs": c} for s, c in sorted(systems.items())],
            }
        )
    return decisions
