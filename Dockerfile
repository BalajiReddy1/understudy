# One container with FalkorDB and the app together, so it runs on any host that takes a
# single Docker image, and locally with one `docker compose up`. The org graph loads
# from data/snapshot on start: no GitHub token, no raw data, no keys needed.

FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM falkordb/falkordb-server:6.0.2
COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*
ENV UV_PYTHON=python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY backend/ backend/
RUN uv sync --frozen --no-dev
COPY data/snapshot/ data/snapshot/
COPY data/proof.json data/
COPY --from=web /web/dist frontend/dist
COPY docker/ docker/

ENV FALKORDB_HOST=127.0.0.1 \
    FALKORDB_PORT=6379 \
    FALKORDB_DIR=/app/rdb \
    PORT=8000

# Load the graphs now, on the build machine, and keep FalkorDB's saved file, so a
# container starts by reading it instead of working out every number again.
RUN mkdir -p "$FALKORDB_DIR" \
    && redis-server --bind 127.0.0.1 --port "$FALKORDB_PORT" --dir "$FALKORDB_DIR" \
       --dbfilename graphs.rdb --save "" --daemonize yes \
       --loadmodule "$FALKORDB_BIN_PATH/falkordb.so" $FALKORDB_ARGS \
    && until redis-cli -p "$FALKORDB_PORT" ping >/dev/null 2>&1; do sleep 0.2; done \
    && .venv/bin/python docker/prebake.py \
    && redis-cli -p "$FALKORDB_PORT" save \
    && redis-cli -p "$FALKORDB_PORT" shutdown nosave
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s \
    CMD python3 -c "import urllib.request, os; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ['PORT'])"
# Through sh, so it runs whether or not the checkout kept the executable bit.
ENTRYPOINT ["sh", "/app/docker/start.sh"]
