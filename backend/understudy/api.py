"""HTTP API. Reads come from the base graph, or from a visitor's own session copy when
?session=<id> is given. The base graph is never written to: rehearsals run on
throwaway copies, and departures a visitor applies go to their own session.

uv run uvicorn understudy.api:app --port 8000
"""

import os
from contextlib import asynccontextmanager
from dataclasses import asdict

from falkordb import FalkorDB
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis.exceptions import ConnectionError as RedisConnectionError

from .db import connect
from .engine.refresh import refresh
from .engine.rehearse import UnknownPeople, depart, rehearse
from .engine.sessions import Session, SessionGone, Sessions
from .engine.stage import call_board, stage

MAX_PEOPLE = 10
# Throwaway copies a crash could leave behind; dropped when the app starts.
SCRATCH_PREFIXES = ("rehearsal:", "scratch:")


class PeopleRequest(BaseModel):
    people: list[str] = Field(min_length=1, max_length=MAX_PEOPLE)


def create_app(graph: str | None = None, db: FalkorDB | None = None) -> FastAPI:
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

    @app.post("/api/sessions/{session_id}/reset")
    def reset_session(session_id: str) -> dict:
        return describe(sessions.reset(session_id))

    @app.delete("/api/sessions/{session_id}")
    def end_session(session_id: str) -> dict:
        sessions.end(session_id)
        return {"ok": True}

    return app


app = create_app()
