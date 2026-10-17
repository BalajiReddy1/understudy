#!/bin/sh
# FalkorDB in the background, reachable only inside the container, then the app in
# front of it. FalkorDB reads the graphs saved while the image was built and never
# writes back, so every start begins from the same state.
set -e

redis-server --bind 127.0.0.1 --port "$FALKORDB_PORT" --dir "$FALKORDB_DIR" \
    --dbfilename graphs.rdb --save "" --appendonly no \
    --daemonize yes --loadmodule "$FALKORDB_BIN_PATH/falkordb.so" $FALKORDB_ARGS

# PONG, not just an answer: while it reads the saved graphs it replies LOADING.
until [ "$(redis-cli -p "$FALKORDB_PORT" ping 2>/dev/null)" = "PONG" ]; do
    sleep 0.2
done

# Behind a host's proxy every visitor arrives from the proxy's address, which would put
# the whole internet in one bucket of the question limit. --proxy-headers reads the
# visitor's address from X-Forwarded-For instead. A client can fake that header; the
# most it gets is more questions on the free model key, and the agent falls back to
# the plain graph tools when the model is busy.
exec /app/.venv/bin/uvicorn understudy.api:app --host 0.0.0.0 --port "$PORT" \
    --proxy-headers --forwarded-allow-ips "*"
