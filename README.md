# dev-playbook

An MCP server skeleton: SSE transport, local auth with issuable bearer tokens,
a browser dashboard, and per-call usage telemetry.

> **v1.0.0 removed the standards feature from MCP.** This repo used to serve a
> corpus of team rule docs over MCP (`playbook_start` / `playbook_get` /
> `playbook_find`) with a BM25 index and a doc validator. The BM25 index,
> loader, search, validator, quality rules and the three tools have been
> deleted. What remains is the server infrastructure those tools were built
> on, so a new tool surface can be built on it.
>
> **The MCP server currently advertises no tools.** It connects, authenticates
> and records calls, but `tools/list` returns an empty list.
>
> **The dashboard's Standards page was rebuilt as a lightweight,
> self-contained module** that reads `standards/` straight off disk
> (`mcp/standards_scanner.py`) - it has no dependency on the deleted
> corpus/loader and doesn't back any MCP tool. `MCP_STANDARDS_ROOT` still
> configures where it looks, and the Docker image still bakes in `standards/`.
>
> The last version with the full playbook surface is tagged in git history at
> commit `92c00d2` (v0.9.0).

---

## What is still here

| Piece | File | Does |
|---|---|---|
| MCP transport | `mcp/server.py` | SSE endpoint, session routing, call dispatch + timing |
| Auth | `mcp/auth.py`, `mcp/identity.py` | Local users, pbkdf2 password hashes, opaque bearer tokens |
| Dashboard sessions | `mcp/session.py` | HttpOnly cookie session + CSRF double-submit |
| Telemetry | `mcp/metrics.py` | SQLite: registrations, per-call rows, adoption/latency aggregates |
| Dashboard | `mcp/dashboard/` | Users, tools, searches, activity, setup, tokens, user admin, standards |
| Standards scanner | `mcp/standards_scanner.py` | Reads `standards/` off disk for the dashboard's corpus-health pages |

## Run it

```bash
cd mcp
uv sync
uv run server.py
```

Or with Docker:

### Docker Compose (recommended)

```bash
cp .env.example .env      # set MCP_ADMIN_PASSWORD
docker compose up -d
```

### Docker Run (standalone)

```bash
docker build -t dev-playbook .
docker run -d \
  --name dev-playbook \
  -p 127.0.0.1:3001:3000 \
  -e MCP_ADMIN_PASSWORD=changeme \
  -v playbook-data:/data \
  --restart unless-stopped \
  dev-playbook
```

Dashboard: `http://localhost:3001/dashboard/` · MCP (SSE): `http://localhost:3001/sse`

Host port is 3001 because 3000 is commonly taken on a dev box; the container
listens on 3000 internally. The port is published to `127.0.0.1` only.

Get a bearer token for an MCP client:

```bash
curl -s -X POST http://localhost:3001/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<your MCP_ADMIN_PASSWORD>"}'
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MCP_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` for LAN. |
| `MCP_PORT` | `3000` | HTTP port. |
| `MCP_CONFIG` | `mcp/config.toml` | Override config file path. |
| `MCP_DB_PATH` | `mcp/data/metrics.db` | SQLite file for usage metrics + auth. |
| `MCP_INACTIVE_DAYS` | `2` | Days without a tool call before a user is "inactive". |
| `MCP_SERVER_LABEL` | `dev-playbook` | Display name in the dashboard and MCP registration. |
| `MCP_ADMIN_USER` | `admin` | Default admin username (seeded on first boot). |
| `MCP_ADMIN_PASSWORD` | `admin` | Default admin password. **Required** (non-default) when `MCP_HOST=0.0.0.0`. |
| `MCP_STANDARDS_ROOT` | `<repo>/standards` | Directory the dashboard's Standards page scans for rule-doc corpora. |

Auth is entirely local: create users in `/dashboard/users-admin`, issue MCP
tokens in `/dashboard/tokens`, and authenticate clients with
`Authorization: Bearer <token>`.

## Adding a tool surface

Register tools on the `Server` instance in `mcp/server.py`. `list_tools()`
returns `[]` today; `_dispatch_typed()` is the single dispatch point and already
records status, latency and per-call context into metrics.

## Development

```bash
cd mcp
uv run pytest                  # 112 tests
uv run ruff check .
uv run ruff format --check .
```

## License

See [LICENSE](LICENSE).
