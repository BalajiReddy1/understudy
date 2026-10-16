"""HTTP API. Reads come from the base graph, or from a visitor's own session copy when
?session=<id> is given. The base graph is never written to: rehearsals and fix trials
run on throwaway copies, and departures or fixes a visitor applies go to their session.

uv run uvicorn understudy.api:app --port 8000
"""

import json
import logging
import os
import queue
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal

from falkordb import FalkorDB
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from redis.exceptions import ConnectionError as RedisConnectionError

from .agent import memory
from .agent.loop import Reply, ask
from .agent.tools import Context
from .agent.trace import Recorder, TracedDB
from .db import connect
from .engine.fixes import FixRejected, UnknownSystem, apply_fix, checked_fix, propose
from .engine.refresh import refresh
from .engine.rehearse import UnknownPeople, depart, rehearse
from .engine.sessions import Session, SessionGone, Sessions
from .engine.stage import call_board, stage

MAX_PEOPLE = 10
log = logging.getLogger("understudy")
# Throwaway copies a crash could leave behind; dropped when the app starts.
SCRATCH_PREFIXES = ("rehearsal:", "scratch:", "fix:")


class PeopleRequest(BaseModel):
    people: list[str] = Field(min_length=1, max_length=MAX_PEOPLE)


class FixRequest(BaseModel):
    system: str
    kind: Literal["pair", "approve"]
    knower: str
    approver: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    history: list[dict] = Field(default_factory=list, max_length=12)


class AskLimiter:
    """At most `count` questions per `seconds` from one address, so one visitor can't
    spend the free model's quota for everyone else. Everything but asking still works."""

    def __init__(self, count: int, seconds: float):
        self.count, self.seconds = count, seconds
        self.seen: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, who: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.seen[who]
            while q and now - q[0] > self.seconds:
                q.popleft()
            if len(q) >= self.count:
                return False
            q.append(now)
            return True


def create_app(
    graph: str | None = None,
    db: FalkorDB | None = None,
    ask_client=None,
    ask_limit: tuple[int, float] = (6, 600),
) -> FastAPI:
    graph = graph or os.environ.get("UNDERSTUDY_GRAPH", "org")
    # The client connects as soon as it's made, so it's made on first use: if FalkorDB
    # is still starting, the app answers 503 and tries again on the next request
    # instead of failing to start at all.
    conn = {"db": db}

    def get_db() -> FalkorDB:
        if conn["db"] is None:
            conn["db"] = connect()
        return conn["db"]

    sessions = Sessions(get_db, graph)
    limiter = AskLimiter(*ask_limit)

    def built() -> bool:
        return graph in get_db().list_graphs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            names = get_db().list_graphs()
            for name in names:
                if name.startswith(SCRATCH_PREFIXES):
                    get_db().select_graph(name).delete()
            sessions.drop_orphans()
            # A graph loaded without numbers (say, from a snapshot) gets them on start,
            # so the call board and the rehearsal shortcut always have CARRIES to read.
            if graph in names:
                missing = (
                    get_db()
                    .select_graph(graph)
                    .ro_query("MATCH (s:System) WHERE s.understudy IS NULL RETURN count(s)")
                    .result_set[0][0]
                )
                if missing:
                    refresh(get_db(), graph)
        except RedisConnectionError:
            pass  # /api/health reports it; requests get a 503 until FalkorDB is up
        yield

    app = FastAPI(title="Understudy", lifespan=lifespan)

    @app.exception_handler(RedisConnectionError)
    async def graph_down(request: Request, exc: RedisConnectionError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "The graph database isn't answering. Try again in 30 seconds."},
        )

    @app.exception_handler(SessionGone)
    async def session_gone(request: Request, exc: SessionGone) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": "This session has expired or was reset. Start a new one."},
        )

    @app.exception_handler(UnknownPeople)
    async def unknown_people(request: Request, exc: UnknownPeople) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Nobody in the graph is called {', '.join(exc.handles)}."},
        )

    def check_built() -> None:
        if not built():
            raise HTTPException(
                503, f"The graph '{graph}' hasn't been built yet. Run scripts/build_org.py."
            )

    def target(session: str | None) -> str:
        """The graph a request reads: the visitor's session copy, or the base graph."""
        check_built()
        return sessions.get(session).graph if session else graph

    @app.get("/api/health")
    def health() -> dict:
        check_built()
        g = get_db().select_graph(graph)
        systems = g.ro_query("MATCH (s:System) RETURN count(s)").result_set[0][0]
        return {"ok": True, "graph": graph, "systems": systems}

    @app.get("/api/systems")
    def systems(session: str | None = None) -> list[dict]:
        return call_board(get_db().select_graph(target(session)))

    @app.get("/api/systems/{name:path}")
    def system(name: str, session: str | None = None) -> dict:
        found = stage(get_db().select_graph(target(session)), name)
        if found is None:
            raise HTTPException(404, f"No system called '{name}'. GET /api/systems lists them.")
        return found

    @app.post("/api/rehearse")
    def rehearse_departure(body: PeopleRequest, session: str | None = None) -> dict:
        return asdict(rehearse(get_db(), target(session), body.people))

    @app.get("/api/fixes/{name:path}")
    def fixes(name: str, session: str | None = None) -> dict:
        try:
            return asdict(propose(get_db(), target(session), name))
        except UnknownSystem as e:
            raise HTTPException(404, f"No system called '{name}'.") from e

    # --- sessions -----------------------------------------------------------------

    def describe(s: Session) -> dict:
        return {"id": s.id, "changes": s.changes}

    @app.post("/api/sessions")
    def start_session() -> dict:
        check_built()
        return describe(sessions.start())

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        return describe(sessions.get(session_id))

    @app.post("/api/sessions/{session_id}/depart")
    def session_depart(session_id: str, body: PeopleRequest) -> dict:
        s = sessions.get(session_id)
        result = depart(get_db(), s.graph, body.people)
        s.changes.append({"type": "depart", "people": result.people})
        return asdict(result)

    @app.post("/api/sessions/{session_id}/fix")
    def session_fix(session_id: str, body: FixRequest) -> dict:
        s = sessions.get(session_id)
        g = get_db().select_graph(s.graph)
        try:
            fix = checked_fix(g, body.system, body.kind, body.knower, body.approver)
        except FixRejected as e:
            raise HTTPException(e.status, e.message) from e
        changes = apply_fix(get_db(), s.graph, fix)
        s.changes.append({"type": "fix", **body.model_dump()})
        return {"changes": [asdict(c) for c in changes]}

    # --- the agent ------------------------------------------------------------------

    def ask_context(session: str | None, visitor: str | None) -> tuple[Context, dict]:
        """A context for one question. Reads follow the visitor's session copy; if the
        agent makes a change and there is no session yet, one is started and its id
        goes back with the answer. Memory goes to the visitor's own graph."""
        check_built()
        if visitor is not None:
            try:
                memory.graph_name(visitor)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
        current: dict = {"id": session}
        reads = sessions.get(session).graph if session else graph

        def make_session() -> tuple[str, str]:
            if current["id"] is None:
                current["id"] = sessions.start().id
            s = sessions.get(current["id"])
            return s.id, s.graph

        ctx = Context(TracedDB(get_db(), Recorder()), reads, visitor=visitor, session=make_session)
        return ctx, current

    def answered(reply: Reply, current: dict) -> dict:
        if current["id"]:
            changes = sessions.get(current["id"]).changes
            for s in reply.steps:
                if s.status == "ok" and s.tool in ("take_off_stage", "apply_fix"):
                    changes.append({"type": "agent", "tool": s.tool, **s.args})
        return {
            "text": reply.text,
            "kind": reply.kind,
            "checked": reply.checked,
            "note": reply.note,
            "model": reply.model,
            "session": current["id"],
            "steps": [asdict(s) for s in reply.steps],
        }

    def check_limit(request: Request) -> None:
        if not limiter.allow(request.client.host if request.client else "unknown"):
            raise HTTPException(
                429,
                "That's the question limit for now (it keeps the free model available for "
                "everyone). The call board, stages and rehearsals still work.",
            )

    @app.post("/api/ask")
    def ask_json(
        body: AskRequest, request: Request, session: str | None = None, visitor: str | None = None
    ) -> dict:
        check_limit(request)
        ctx, current = ask_context(session, visitor)
        return answered(ask(body.question, ctx, body.history, client=ask_client), current)

    @app.post("/api/ask/stream")
    def ask_stream(
        body: AskRequest, request: Request, session: str | None = None, visitor: str | None = None
    ) -> StreamingResponse:
        """The same as /api/ask, as server-sent events: one per tool call as it
        finishes, notices (the model is busy, a tool is being checked), then the answer."""
        check_limit(request)
        ctx, current = ask_context(session, visitor)
        events: queue.Queue = queue.Queue()

        def work() -> None:
            try:
                reply = ask(body.question, ctx, body.history, client=ask_client, emit=events.put)
                events.put({"type": "answer", **answered(reply, current)})
            except Exception:
                log.exception("ask failed")
                events.put({"type": "error", "text": "Something went wrong answering that."})
            finally:
                events.put(None)

        threading.Thread(target=work, daemon=True).start()

        def stream():
            while (e := events.get()) is not None:
                yield f"data: {json.dumps(e, default=str)}\n\n"

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.post("/api/sessions/{session_id}/reset")
    def reset_session(session_id: str) -> dict:
        return describe(sessions.reset(session_id))

    @app.delete("/api/sessions/{session_id}")
    def end_session(session_id: str) -> dict:
        sessions.end(session_id)
        return {"ok": True}

    return app


app = create_app()
