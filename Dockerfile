# syntax=docker/dockerfile:1

# ---- build: resolve dependencies into a self-contained venv -----------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app/mcp

# Lockfile first: dependencies re-resolve only when they actually change.
COPY mcp/pyproject.toml mcp/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project


# ---- runtime ----------------------------------------------------------------
FROM python:3.12-slim-bookworm

# Non-root: the server only ever reads the corpora, and writes solely to
# /data (metrics + auth SQLite).
RUN useradd --system --create-home --uid 10001 playbook

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=3000 \
    MCP_DB_PATH=/data/metrics.db \
    MCP_STANDARDS_ROOT=/app/standards \
    MCP_REQUIREMENTS_ROOT=/app/requirements

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY mcp/ /app/mcp/
COPY scripts/ /app/scripts/
# Baked-in corpora, so the image runs standalone. Compose bind-mounts over
# these for live editing.
COPY standards/ /app/standards/
COPY requirements/ /app/requirements/

RUN mkdir -p /data && chown -R playbook:playbook /data /app
USER playbook

WORKDIR /app/mcp
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3000/healthz', timeout=2).status==200 else 1)"

CMD ["python", "server.py"]
