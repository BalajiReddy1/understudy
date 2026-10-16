"""Per-visitor copies of the company. Each visitor who changes something gets their own
GRAPH.COPY of the base graph, so nobody can break the demo for the next person.
Sessions expire after two idle hours, and at most 200 exist at once (the one used
longest ago goes first). Copies left over from a previous run are dropped at start."""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from falkordb import FalkorDB

from ..db import copy

PREFIX = "session:"
TTL_SECONDS = 2 * 60 * 60
MAX_SESSIONS = 200


class SessionGone(KeyError):
    pass


@dataclass
class Session:
    id: str
    created: float
    last_used: float
    changes: list[dict] = field(default_factory=list)  # what the visitor did, in order

    @property
    def graph(self) -> str:
        return PREFIX + self.id


class Sessions:
    def __init__(
        self,
        db: Callable[[], FalkorDB],
        base: str,
        ttl: float = TTL_SECONDS,
        cap: int = MAX_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.db, self.base, self.ttl, self.cap, self.clock = db, base, ttl, cap, clock
        self.items: dict[str, Session] = {}
        self.lock = threading.Lock()

    def drop_orphans(self) -> None:
        """Drop session copies this process doesn't know about (from a restart)."""
        db = self.db()
        with self.lock:
            for name in db.list_graphs():
                if name.startswith(PREFIX) and name[len(PREFIX) :] not in self.items:
                    db.select_graph(name).delete()

    def _drop(self, s: Session) -> None:
        self.items.pop(s.id, None)
        db = self.db()
        if s.graph in db.list_graphs():
            db.select_graph(s.graph).delete()

    def _sweep(self) -> None:
        now = self.clock()
        for s in [s for s in self.items.values() if now - s.last_used > self.ttl]:
            self._drop(s)

    def start(self) -> Session:
        with self.lock:
            self._sweep()
            while len(self.items) >= self.cap:
                self._drop(min(self.items.values(), key=lambda s: s.last_used))
            now = self.clock()
            s = Session(uuid.uuid4().hex[:16], now, now)
            copy(self.db().select_graph(self.base), s.graph)
            self.items[s.id] = s
            return s

    def get(self, session_id: str) -> Session:
        with self.lock:
            self._sweep()
            s = self.items.get(session_id)
            if s is None:
                raise SessionGone(session_id)
            s.last_used = self.clock()
            return s

    def reset(self, session_id: str) -> Session:
        s = self.get(session_id)
        with self.lock:
            db = self.db()
            db.select_graph(s.graph).delete()
            copy(db.select_graph(self.base), s.graph)
            s.changes.clear()
            return s

    def end(self, session_id: str) -> None:
        s = self.get(session_id)
        with self.lock:
            self._drop(s)
