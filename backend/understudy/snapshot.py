"""The org graph as a file. scripts/build_org.py --snapshot writes it from the raw pull;
the app loads it when its graph doesn't exist, so a fresh clone runs with no GitHub
token, no raw data and no pseudonym salt. It holds pseudonyms only, never logins."""

import gzip
import json
from pathlib import Path

from falkordb import FalkorDB

from .db import drop
from .derive.flow import build_flow_layer, write_org
from .engine.refresh import refresh


def save(org: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(org, sort_keys=True, separators=(",", ":")).encode()
    # No timestamp and no file name in the header, so the same org always gives the same
    # bytes and git sees no change.
    with (
        path.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as f,
    ):
        f.write(data)


def read(path: Path) -> dict:
    with gzip.open(path, "rb") as f:
        return json.loads(f.read())


def load(db: FalkorDB, graph: str, path: Path) -> None:
    """Write the org to `graph` with its flow layer and every number worked out."""
    org = read(path)
    drop(db, graph)
    g = db.select_graph(graph)
    write_org(g, org)
    build_flow_layer(g)
    refresh(db, graph)
