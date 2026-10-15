"""HTTP API. Every endpoint reads the base graph; rehearsals write only to a copy.

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
from .engine.rehearse import UnknownPeople, rehearse
from .engine.stage import call_board, stage
from .engine.understudy import refresh

MAX_PEOPLE = 10


class RehearseRequest(BaseModel):
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

    def built() -> bool:
        return graph in get_db().list_graphs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # A graph loaded without numbers (say, from a snapshot) gets them on start, so
        # the call board and the rehearsal shortcut always have CARRIES to read.
        try:
            if built():
                g = get_db().select_graph(graph)
                missing = g.ro_query(
                    "MATCH (s:System) WHERE s.understudy IS NULL RETURN count(s)"
                ).result_set[0][0]
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

    def base():
        if not built():
            raise HTTPException(
                503, f"The graph '{graph}' hasn't been built yet. Run scripts/build_org.py."
            )
        return get_db().select_graph(graph)

    @app.get("/api/health")
    def health() -> dict:
        g = base()
        systems = g.ro_query("MATCH (s:System) RETURN count(s)").result_set[0][0]
        return {"ok": True, "graph": graph, "systems": systems}

    @app.get("/api/systems")
    def systems() -> list[dict]:
        return call_board(base())

    @app.get("/api/systems/{name:path}")
    def system(name: str) -> dict:
        found = stage(base(), name)
        if found is None:
            raise HTTPException(404, f"No system called '{name}'. GET /api/systems lists them.")
        return found

    @app.post("/api/rehearse")
    def rehearse_departure(body: RehearseRequest) -> dict:
        base()
        try:
            return asdict(rehearse(get_db(), graph, body.people))
        except UnknownPeople as e:
            raise HTTPException(
                404, f"Nobody in the graph is called {', '.join(e.handles)}."
            ) from e

    return app


app = create_app()
