import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

RAW = ROOT / "data" / "raw"
# The backtest's result (scripts/backtest.py), committed so /proof works without data/raw.
PROOF = ROOT / "data" / "proof.json"


def salt() -> str:
    value = os.environ.get("PSEUDONYM_SALT", "")
    if not value or value == "change-me":
        raise SystemExit("Set PSEUDONYM_SALT in .env (any long random string, never committed).")
    return value
