#!/bin/sh
# FalkorDB in the background, reachable only inside the container, then the app in
# front of it. Nothing is saved to disk: the graph loads from the snapshot each start.
set -e

redis-server --bind 127.0.0.1 --port "$FALKORDB_PORT" --save "" --appendonly no \
    --daemonize yes --loadmodule "$FALKORDB_BIN_PATH/falkordb.so" $FALKORDB_ARGS

until redis-cli -p "$FALKORDB_PORT" ping >/dev/null 2>&1; do
    sleep 0.2
done

exec /app/.venv/bin/uvicorn understudy.api:app --host 0.0.0.0 --port "$PORT"
