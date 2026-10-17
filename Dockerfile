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
COPY docker/start.sh /app/start.sh

ENV FALKORDB_HOST=127.0.0.1 \
    FALKORDB_PORT=6379 \
    PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s \
    CMD python3 -c "import urllib.request, os; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ['PORT'])"
# Through sh, so it runs whether or not the checkout kept the executable bit.
ENTRYPOINT ["sh", "/app/start.sh"]
