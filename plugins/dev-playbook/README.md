# dev-playbook - Claude Code plugin

Your team's coding standards, in context: guardrails at session start, the
definition of done before the first edit, and five MCP tools to read the rest.
If a repo has no standards yet, the plugin scaffolds them.

## Install

```
/plugin marketplace add arockiaraj1994/dev-agent-playbook
/plugin install dev-playbook@dev-playbook
```

Then restart Claude Code. That is the whole setup: no server to run, no port,
no bearer token. The plugin launches the server itself over stdio and keeps its
database in the plugin's own data directory, which survives updates and
uninstall.

Requires [`uv`](https://docs.astral.sh/uv/getting-started/installation/) on
`PATH` - it manages the server's Python and dependencies.

## What you get

| Component | Does |
|---|---|
| **MCP server** (5 tools) | `playbook_start_task`, `playbook_get_standard`, `playbook_find_standards`, `playbook_list_templates`, `playbook_scaffold_standards` |
| **`using-standards`** skill | Model-invoked. How to read the standards, and how to name the project on every call. |
| **`/dev-playbook:scaffold-standards`** | Walks a repo with no standards: detect languages, fill placeholders from the source, preview, write. |
| **SessionStart hook** | Puts this project's guardrails in context at session start, without anyone having to ask. |
| **PreToolUse hook** (`Write`/`Edit`) | The definition of done, once per session, before the first edit. |

The hooks read the standards database directly with stdlib `sqlite3` - no MCP
round-trip, nothing to time out. Any failure exits silently: a hook must never
be the reason a session is broken.

## Settings

Configure from `/plugin` → dev-playbook → Configure.

| Setting | Default | For |
|---|---|---|
| `enforce_standards` | `false` | Block the first `Write`/`Edit` when the repo has no standards, instead of only warning. See below. |
| `server_url` | *(empty)* | Team mode: point at a shared dev-playbook server, e.g. `http://localhost:3001`. |
| `token` | *(empty)* | Bearer token for that server, from `POST /auth/login`. Only when it has auth on. |
| `server_path` | *(empty)* | Path to the repo's `mcp/` directory, if the plugin cannot find it next to itself. |

### Enforcement

Off by default: installing a plugin should not stop anyone's work by surprise.

- **Advisory** (default) - the definition of done arrives as context before your
  first edit; a repo with no standards gets one line naming
  `/dev-playbook:scaffold-standards`. Nothing is ever blocked.
- **Enforcing** (`enforce_standards: true`) - a repo with no standards project
  has its `Write`/`Edit` calls denied, with a reason naming what is missing.
  Every edit, not just the first: a gate that closes once is not a gate.

Enforcement never fires when there is no standards database at all - that would
lock a first-run user out of their own repo before they could scaffold anything.

## Local mode vs team mode

**Local** (default) - `scripts/playbook-mcp.sh` runs `server.py --stdio` against
the plugin's own SQLite database. No infrastructure, no dashboard.

**Team** - set `server_url` and the same script runs `scripts/sse_bridge.py`
instead, which pumps JSON-RPC between Claude Code's stdio and the shared
server's SSE endpoint. The shared server has the dashboard and everyone's
telemetry; the bridge inspects nothing, so the tools you see are exactly what
that server advertises.

If the bridge gives you trouble, the manual path still works and is unaffected:

```
claude mcp add --transport sse dev-playbook http://localhost:3001/sse \
  --header "Authorization: Bearer <token>"
```

## Layout

```
plugins/dev-playbook/
├─ .claude-plugin/plugin.json     # only plugin.json goes in here
├─ .mcp.json                      # the MCP server, at the plugin root
├─ skills/using-standards/SKILL.md
├─ skills/scaffold-standards/SKILL.md
├─ hooks/hooks.json
├─ scripts/playbook-mcp.sh        # transport selector
├─ scripts/sse_bridge.py          # stdio <-> SSE, team mode only
├─ scripts/session_context.py     # SessionStart hook
├─ scripts/edit_gate.py           # PreToolUse hook
└─ scripts/playbook_db.py         # shared stdlib DB reader for the hooks
```

The plugin versions independently of the server: `0.1.0` here, `1.1.0` for the
MCP server in `mcp/`.
