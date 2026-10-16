import os
import time

from falkordb import FalkorDB, Graph
from redis.exceptions import ResponseError


def connect(port: int | None = None) -> FalkorDB:
    return FalkorDB(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=port or int(os.environ.get("FALKORDB_PORT", "6379")),
    )


def drop(db: FalkorDB, name: str) -> None:
    if name in db.list_graphs():
        db.select_graph(name).delete()


def copy(g: Graph, name: str, attempts: int = 100) -> Graph:
    """GRAPH.COPY, retried while the server is busy forking.

    FalkorDB 4.22 copies a graph by forking the server, and Redis allows one child
    process at a time, so two copies at once fail with "could not fork". 6.0.2 doesn't
    fork and never hits this. Waiting a moment and trying again keeps both working."""
    for attempt in range(attempts):
        try:
            return g.copy(name)
        except ResponseError as e:
            if "could not fork" not in str(e) or attempt == attempts - 1:
                raise
            time.sleep(0.02 * min(attempt + 1, 10))
    raise AssertionError("unreachable")
