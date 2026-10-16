"""A recording wrapper around the FalkorDB client, so every answer can show the exact
Cypher behind it, with timings, without the engine knowing it is being watched.

TracedDB stands in for FalkorDB and hands out TracedGraphs; both pass everything
through. Engine code runs queries from worker threads, so the recorder is locked."""

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from falkordb import FalkorDB, Graph


@dataclass
class Query:
    graph: str
    cypher: str
    ms: float
    rows: int


@dataclass
class Recorder:
    queries: list[Query] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, q: Query) -> None:
        with self.lock:
            self.queries.append(q)

    def take(self) -> list[Query]:
        """Everything recorded so far, clearing the list."""
        with self.lock:
            out, self.queries = self.queries, []
            return out


def _graph_kind(name: str) -> str:
    # Copies have random names; the kind is what a reader needs.
    return name.split(":", 1)[0] + ":*" if ":" in name else name


class TracedGraph:
    def __init__(self, g: Graph, rec: Recorder):
        self._g, self._rec = g, rec

    def _run(self, method: str, cypher: str, *args: Any, **kwargs: Any):
        t0 = time.perf_counter()
        result = getattr(self._g, method)(cypher, *args, **kwargs)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        self._rec.add(
            Query(_graph_kind(self._g.name), " ".join(cypher.split()), ms, len(result.result_set))
        )
        return result

    def query(self, cypher: str, *args: Any, **kwargs: Any):
        return self._run("query", cypher, *args, **kwargs)

    def ro_query(self, cypher: str, *args: Any, **kwargs: Any):
        return self._run("ro_query", cypher, *args, **kwargs)

    def copy(self, name: str) -> "TracedGraph":
        t0 = time.perf_counter()
        g = self._g.copy(name)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        self._rec.add(Query(_graph_kind(name), f"GRAPH.COPY {self._g.name}", ms, 0))
        return TracedGraph(g, self._rec)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._g, name)


class TracedDB:
    def __init__(self, db: FalkorDB, rec: Recorder):
        self._db, self.recorder = db, rec

    def select_graph(self, name: str) -> TracedGraph:
        return TracedGraph(self._db.select_graph(name), self.recorder)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)
