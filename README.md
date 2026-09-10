# dev-playbook

Your team's coding standards, served to coding agents over MCP - and
bootstrapped by them. An agent sitting in a repo with no standards can generate
a full set from the language template packs, then read them back on every task.

Ships an MCP server (stdio or SSE) with local auth and issuable bearer tokens,
a browser dashboard for authoring and telemetry, and a SQLite store both share.

## Install as a Claude Code plugin

The one-command path. No server to run, no port, no bearer token:

```
/plugin marketplace add arockiaraj1994/dev-agent-playbook
/plugin install dev-playbook@dev-playbook
```

Restart Claude Code and you have the five tools, two skills, and hooks that put
this repo's guardrails and definition of done in context without anyone asking.
The plugin launches the server itself over stdio and keeps its database in the
plugin's own data directory. It needs [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
on `PATH`, and nothing else.

Pointing a team at a shared server, enforcement, and the rest of the settings
are in [`plugins/dev-playbook/README.md`](plugins/dev-playbook/README.md).

Cursor and Windsurf have no plugin system, so they use the manual MCP setup
further down.

## The tool surface

| Tool | Does | Writes |
|---|---|---|
| `playbook_start_task(project, intent)` | The entry point. Returns the guardrails plus the workflow matching what you are about to do, and the refs to read next. | no |
| `playbook_get_standard(project, ref)` | Reads one document. `ref` is a path (`core/guardrails.md`) or shorthand (`guardrails`, `workflow:bug-fix`). | no |
| `playbook_find_standards(project, query?, type?, top_k?)` | Searches a project's standards, or lists them all when given no query. | no |
| `playbook_list_templates(language?)` | The language pack catalog: rule counts, required placeholders, per-pack detail. | no |
| `playbook_scaffold_standards(project, languages[], ...)` | Creates a standards project from base + language packs. Run with `dry_run=true` first. | **yes** |

Every tool declares its MCP annotations, so a client knows which one to confirm
before calling. `playbook_scaffold_standards` is additive but not idempotent -
it creates projects and never merges into one.

### Bootstrapping a repo that has no standards

```
playbook_list_templates()                       # which languages are available
playbook_scaffold_standards(project="billing", languages=["java"],
    placeholders={"package": "com.acme.billing"}, dry_run=true)
                                                # manifest; nothing written
playbook_scaffold_standards(..., dry_run=false) # write it
playbook_start_task(project="billing", intent="add pagination to /orders")
```

The tool and the dashboard's creation wizard both call `mcp/scaffold_service.py`,
so the two paths apply identical rules - a test diffs the resulting stores to
keep it that way.

### Authoring from the browser

The dashboard's Standards page reads the same SQLite store
(`standards_projects` / `standards_files` in `metrics.db`).
`mcp/standards_scanner.py` scores corpus health against store rows. Admins
create projects through a six-step wizard, and edit any document in a four-tab
viewer (Formatted / Source / Code / Edit). Initial content ships as
`mcp/data/standards_seed.json` and loads on first boot.

---

## What is here

| Piece | File | Does |
|---|---|---|
| MCP transport | `mcp/server.py` | SSE endpoint and stdio transport, session routing, call dispatch + timing |
| Auth | `mcp/auth.py`, `mcp/identity.py` | Local users, pbkdf2 password hashes, opaque bearer tokens |
| Dashboard sessions | `mcp/session.py` | HttpOnly cookie session + CSRF double-submit |
| Telemetry | `mcp/metrics.py` | SQLite: registrations, per-call rows, adoption/latency aggregates |
| Dashboard | `mcp/dashboard/` | Users, tools, searches, activity, setup, tokens, user admin, standards |
| Standards store | `mcp/standards_store.py` | SQLite DAL for the standards corpus (CRUD, optimistic concurrency, seed/dump) |
| Standards scanner | `mcp/standards_scanner.py` | Runs validation rules against store rows for the dashboard's corpus-health pages |
| Tool surface | `mcp/tools/` | One module per tool, each exporting `DEFINITIONS` + `dispatch`; `server.py` concatenates and routes them |
| Ref grammar | `mcp/tools/refs.py` | How a tool call addresses one document; shared by the tools and the Next Calls renderer |
| Template packs | `mcp/templates/` | The base pack plus one per language; format contract in `TEMPLATE_SPEC.md` |
| Template loader | `mcp/templates_source.py`, `mcp/templates_store.py` | Finds packs on disk, then loads, validates and composes them into documents |
| Scaffolding | `mcp/scaffold_service.py` | The single entry point both the MCP tool and the dashboard wizard call |
| Claude Code plugin | `plugins/dev-playbook/` | Manifest, MCP server entry, two skills, SessionStart + PreToolUse hooks |
| Marketplace | `.claude-plugin/marketplace.json` | Self-hosted marketplace; one relative entry, so there is one version to bump |

## Run it

```bash
cd mcp
uv sync
uv run server.py            # HTTP + SSE on :3000, plus the dashboard
uv run server.py --stdio    # MCP over stdio; no port, no dashboard
```

Both transports share one `Server` instance and one `_initialization_options()`,
so the tool surface is identical across them by construction. `--stdio` is what
the Claude Code plugin launches; SSE is what a shared team instance runs.

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
| `MCP_ADMIN_PASSWORD` | `admin` | Admin password, seeded on first boot. No password is committed to `config.toml`; this is the only way to set one. **Required** (non-default) when `MCP_HOST=0.0.0.0`. |
| `MCP_EDITOR` | `claude-code` | Under `--stdio`, the client name recorded in telemetry. Over SSE this comes from the `User-Agent` instead. |
| `MCP_STANDARDS_SEED` | `mcp/data/standards_seed.json` | JSON seed file loaded into the standards tables on first boot (only when they're empty). |
| `MCP_TEMPLATE_CACHE` | `~/.cache/dev-playbook-templates` | Extra template pack search path, searched before the bundled `mcp/templates/`. Point it at a checkout of a private template repo to override the bundled packs by id - see "Keeping your own packs in a private repo" in [`TEMPLATE_SPEC.md`](mcp/templates/TEMPLATE_SPEC.md). |

`config.toml` carries two switches: `[enable] auth` (default false) and
`[enable] scaffold` (default true). With `scaffold = false` the write tool is
neither advertised nor callable and the read tools are unaffected. With auth
enabled, scaffolding additionally requires an admin token; with auth disabled
there are no roles to check, so the local operator may scaffold.

`config.toml` carries no password. The admin password comes from
`MCP_ADMIN_PASSWORD` - a committed credential ends up in git history and in
every clone. With it unset the seeded password is `admin`, which the server
refuses to start on when `MCP_HOST=0.0.0.0`, and `docker compose` refuses to
start at all.

Auth is entirely local: create users in `/dashboard/users-admin`, issue MCP
tokens in `/dashboard/tokens`, and authenticate clients with
`Authorization: Bearer <token>`. Under `--stdio` there is no transport to carry
a token, so auth does not apply - a local stdio server is one trusted operator.

## Adding a tool

Add a module under `mcp/tools/` exporting `DEFINITIONS: list[Tool]` and an async
`dispatch(name, arguments, ctx, store)` that returns `None` for names it does not
own, then list it in `_TOOL_MODULES` in `mcp/server.py`. Dispatch, timing and
per-call telemetry are already wired around it. Annotate every tool: a client
that sees no annotations is entitled to assume the worst.

## Development

```bash
cd mcp
uv run pytest                  # 662 tests
uv run ruff check . ../plugins
uv run ruff format --check . ../plugins
cd .. && python3 scripts/validate_plugin.py   # plugin manifests, layout, hooks
```

## License

See [LICENSE](LICENSE).
