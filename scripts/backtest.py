"""Run the backtest behind /proof and write data/proof.json.

uv run python scripts/backtest.py

The method is in backend/understudy/backtest.py. It builds the org graph as it stood
on each departure date, so FalkorDB has to be running, and it needs data/raw.
"""

import json
import time

from understudy.backtest import run
from understudy.config import PROOF, RAW, salt
from understudy.db import connect
from understudy.derive.departures import departures
from understudy.derive.kubernetes import all_logins, load_aliases, load_prs
from understudy.derive.people import pseudonyms


def main() -> None:
    t0 = time.perf_counter()
    prs, aliases = load_prs(RAW), load_aliases(RAW)
    names = pseudonyms(all_logins(prs), salt())  # the same names the org graph uses
    result = run(connect(), RAW, prs, aliases, departures(RAW, prs), names, salt())
    PROOF.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")

    s = result["summary"]
    print(f"\nwrote {PROOF.name} in {time.perf_counter() - t0:.0f}s")
    print(f"{len(result['events'])} departures, {s['departures']} used, {s['systems']} systems")
    for e in result["events"]:
        print(f"  {e['left_at']} {e['person']}: {e['reason'] or 'used'}")
    for name, r in s["rho"].items():
        print(f"rho {name}: {r['value']} [{r['low']}, {r['high']}]")
    for name, g in s["groups"].items():
        print(f"{name} group: n={g['n']} median slowdown {g['value']} [{g['low']}, {g['high']}]")


if __name__ == "__main__":
    main()
