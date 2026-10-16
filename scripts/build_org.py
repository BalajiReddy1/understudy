"""Build the `org` graph from data/raw and compute every system's understudy number.

uv run python scripts/build_org.py                       # as of 2026-10-14
uv run python scripts/build_org.py --as-of 2025-06-30 --graph org@2025-06
"""

import argparse
import time
from collections import Counter
from datetime import date

from understudy.config import RAW, salt
from understudy.db import connect, drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.derive.kubernetes import derive_org, load_aliases, load_owners, load_prs
from understudy.engine.refresh import refresh


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 10, 14))
    ap.add_argument("--graph", default="org")
    args = ap.parse_args()

    t0 = time.perf_counter()
    prs, owners, aliases = load_prs(RAW), load_owners(RAW, args.as_of), load_aliases(RAW)
    org, _ = derive_org(prs, owners, aliases, args.as_of, salt())
    t1 = time.perf_counter()
    print(
        f"derived from {len(prs)} PRs in {t1 - t0:.1f}s: {len(org['systems'])} systems, "
        f"{len(org['teams'])} approver teams, {len(org['people'])} people, "
        f"{len(org['knows'])} KNOWS, {len(org['collaborates'])} COLLABORATES pairs"
    )

    db = connect()
    drop(db, args.graph)
    g = db.select_graph(args.graph)
    write_org(g, org)
    build_flow_layer(g)
    t2 = time.perf_counter()
    print(f"wrote {args.graph} and its flow layer in {t2 - t1:.1f}s")

    results = refresh(db, args.graph)
    t3 = time.perf_counter()
    print(f"max-flow, CARRIES and CRITICAL for {len(results)} systems in {t3 - t2:.1f}s")
    report(g)


def report(g) -> None:
    rows = g.ro_query(
        """MATCH (s:System)<-[:DEPENDS_ON]-(t:Team)
           OPTIONAL MATCH (t)<-[:MEMBER_OF]-(a:Person)
           WITH s, t, count(DISTINCT a) AS approvers
           OPTIONAL MATCH (s)<-[:KNOWS]-(k:Person)
           WITH s, t, approvers, count(DISTINCT k) AS knowers
           RETURN s.name, t.name, s.sigs, approvers, knowers,
                  s.understudy, s.emeritus_approvers, s.inactive_owners"""
        # Aggregation ends in a WITH: see the 6.0.2 crash note in engine/stage.py.
    ).result_set

    print("\nunderstudy number -> systems:", dict(sorted(Counter(r[5] for r in rows).items())))
    below_approvers = [r for r in rows if r[5] < r[3]]
    print(
        f"{len(below_approvers)} of {len(rows)} systems score below their active approver "
        "count, so the bottleneck is how knowledge reaches the approvers, not headcount"
    )
    print(f"{sum(r[3] == 0 for r in rows)} systems have no active approver at all")
    abandoned = [r for r in rows if r[7]]
    print(
        f"{len(abandoned)} systems had to fall back past an OWNERS file nobody listed is active in:"
    )
    for r in abandoned:
        print(f"    {r[0]}: {r[7]} -> {r[1]}")

    # The interesting systems are the ones a headcount calls safe: several people know
    # them, yet few independent routes reach the approvers.
    exposed = sorted((r for r in rows if r[4] >= 3), key=lambda r: (r[5], -r[4]))
    print("\nmost exposed (understudy, knowers, active approvers):")
    for name, team, sigs, approvers, knowers, u, emeritus, _ in exposed[:20]:
        note = f", {emeritus} emeritus in its own OWNERS" if emeritus else ""
        print(
            f"  {u:>2} | {knowers:>3} know | {approvers:>2} approve  {name}"
            f"  [{team}; {', '.join(sigs or [])}{note}]"
        )


if __name__ == "__main__":
    main()
