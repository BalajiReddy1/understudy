"""Tests run against every FalkorDB listed in FALKORDB_TEST_PORTS (default 6379).
To check both engines, start 4.22 next to the compose one:

    docker run -d --rm --name understudy-fk4 -p 6391:6379 falkordb/falkordb:v4.22.0
    FALKORDB_TEST_PORTS=6379,6391 uv run pytest
"""

import os

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from understudy.db import connect, drop

PORTS = [int(p) for p in os.environ.get("FALKORDB_TEST_PORTS", "6379").split(",")]


@pytest.fixture(params=PORTS, ids=lambda p: f"port{p}")
def db(request):
    db = connect(request.param)
    try:
        db.connection.ping()
    except RedisConnectionError:
        pytest.skip(f"no FalkorDB on port {request.param}")
    yield db
    for name in db.list_graphs():
        if name.startswith("test:"):
            drop(db, name)
