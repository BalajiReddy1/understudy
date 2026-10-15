"""Build the `org` graph from data/raw and compute every system's understudy number.

uv run python scripts/build_org.py                       # as of 2026-10-14
uv run python scripts/build_org.py --as-of 2025-06-30 --graph org@2025-06
"""

import argparse
import time
from datetime import date

from understudy.config import RAW, salt
from understudy.db import connect, drop
from understudy.derive.flow import build_flow_layer, write_org
from understudy.derive.kubernetes import derive_org, load_aliases, load_owners, load_prs
from understudy.engine.understudy import all_systems, flow


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 10, 14))
    ap.add_argument("--graph", default="org")
    args = ap.parse_args()

    t0 = time.perf_counter()
    prs, owners, aliases = load_prs(RAW), load_owners(RAW), load_aliases(RAW)
    org, _ = derive_org(prs, owners, aliases, args.as_of, salt())
    t1 = time.perf_counter()
    print(
        f"derived from {len(prs)} PRs in {t1 - t0:.1f}s: {len(org['systems'])} systems, "
        f"{len(org['teams'])} SIGs, {len(org['people'])} people, {len(org['knows'])} KNOWS, "
        f"{len(org['collaborates'])} COLLABORATES pairs"
    )

    db = connect()
    drop(db, args.graph)
    g = db.select_graph(args.graph)
    write_org(g, org)
    build_flow_layer(g)
    t2 = time.perf_counter()
    print(f"wrote {args.graph} and its flow layer in {t2 - t1:.1f}s")

    results, carries = [], []
    for s in all_systems(g):
        r = flow(g, s)
        if r is None:
            continue
        number, carriers = r
        results.append({"name": s, "understudy": number})
        carries += [{"person": p, "system": s} for p in carriers]
    t3 = time.perf_counter()
    print(f"max-flow on {len(results)} systems in {t3 - t2:.1f}s")

    g.query(
        "UNWIND $rows AS r MATCH (s:System {name: r.name}) SET s.understudy = r.understudy",
        {"rows": results},
    )
    g.query(
        """UNWIND $rows AS r MATCH (p:Person {handle: r.person}), (s:System {name: r.system})
           MERGE (p)-[:CARRIES]->(s)""",
        {"rows": carries},
    )

    rows = g.ro_query(
        """MATCH (s:System)<-[:DEPENDS_ON]-(t:Team)
           OPTIONAL MATCH (s)<-[k:KNOWS]-(:Person)
           WITH s, collect(DISTINCT t.name) AS sigs, count(DISTINCT k) AS knowers
           RETURN s.name, sigs, knowers, s.understudy
           ORDER BY s.understudy, knowers DESC"""
    ).result_set
    dist = {}
    for _, _, _, u in rows:
        dist[u] = dist.get(u, 0) + 1
    print("understudy number -> systems:", dict(sorted(dist.items())))
    # The interesting systems are the ones a headcount says are safe: several people
    # know them, yet the understudy number is low.
    exposed = sorted((r for r in rows if r[2] >= 3), key=lambda r: (r[3] / r[2], r[3]))
    print("\nmost exposed (understudy / knowers, at least 3 knowers):")
    for name, sigs, knowers, u in exposed[:15]:
        print(f"  {u:>2} of {knowers:>3} knowers  {name}  ({', '.join(sigs)})")


if __name__ == "__main__":
    main()
