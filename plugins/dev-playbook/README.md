# dev-playbook - Claude Code plugin

Your team's coding standards, in context: guardrails at session start, the
definition of done before an edit, and nine MCP tools to read the rest. If a repo
has no standards yet, the plugin scaffolds them.

The plugin connects to a **dev-playbook server you run yourself** - most simply
as a local Docker container - over MCP (SSE). It carries no server of its own.

## Install

```
/plugin marketplace add arockiaraj1994/dev-playbook
/plugin install dev-playbook@dev-playbook
```

Run the server (from a checkout of this repo):

```
docker compose up -d          # serves MCP + dashboard on http://localhost:8420
```

Then, in any repo you want standards on:

```
/dev-playbook-init            # connect the MCP, configure CLAUDE.md, scaffold if needed
```

Restart Claude Code once after the first `/dev-playbook-init` so the user-scope
MCP registration loads. No `uv`, no Python checkout, no bearer token needed for a
local server with auth off.

## What you get

| Component | Does |
|---|---|
| **`/dev-playbook-init`** command | First-run setup: registers the MCP at user scope, updates the global `CLAUDE.md`, arms the edit gate, and checks/creates this repo's standards project. |
| **MCP server** (9 tools) | `playbook_get_agents`, `playbook_get_guardrails`, `playbook_get_standards`, `playbook_get_patterns`, `playbook_get_workflow`, `playbook_get_gates`, `playbook_find_standards`, `playbook_list_templates`, `playbook_scaffold_standards` |
| **`using-standards`** skill | Model-invoked. How to read the standards, and how to name the project on every call. |
| **`/dev-playbook:scaffold-standards`** | Walks a repo with no standards: detect languages, fill placeholders from the source, preview, write. |
| **SessionStart hook** | Puts this project's guardrails in context at session start, without anyone having to ask. |
| **PreToolUse hook** (`Write`/`Edit`) | The definition of done before an edit; blocks edits in a repo that has not been configured (when enforcing). |

The hooks read state with stdlib only - the local standards DB when there is one,
and the marker files `/dev-playbook-init` drops when the server is remote (Docker).
Any failure exits silently: a hook must never be the reason a session is broken.

## Connecting

The bundled `.mcp.json` is a direct SSE entry to `server_url` - no bridge, no
local process - so it works against a Docker-run server out of the box.
`/dev-playbook-init` also registers the same server at **user scope**
(`claude mcp add-json --scope user`), so `dev-playbook` shows up in `claude mcp
list` and in every project. If the tools appear twice, that is the two
registrations; drop the user-scope one with
`claude mcp remove --scope user dev-playbook`.

| Setting | Default | For |
|---|---|---|
| `server_url` | `http://localhost:8420/sse` | Your server's MCP SSE endpoint. A local Docker server, or a shared team server. |
| `token` | *(empty)* | Bearer token, from `POST /auth/login`. Only when the server has auth on. |
| `enforce_standards` | `false` | Force the edit gate on regardless of the marker (see below). |
| `server_path` | *(empty)* | Contributors only: run the server from a source checkout via `scripts/playbook-mcp.sh` instead of Docker. |

### Enforcement (the edit gate)

`/dev-playbook-init` **arms** the gate (via a marker) and marks the repos it
configures. After that, in any repo that has **not** been configured, `Write`/
`Edit` is denied with a reason that names `/dev-playbook-init` - every edit, not
just the first: a gate that closes once is not a gate. A repo is "configured"
when a local standards DB has its project, or init left a per-repo marker (the
path that works when the DB lives in a container). Set `enforce_standards: true`
to force the gate on without running init.

## Contributors: run from source

Instead of Docker you can run the server from a checkout over stdio, wired up
manually:

```
claude mcp add dev-playbook-local -- \
  env PLAYBOOK_SERVER_PATH=/path/to/dev-playbook/mcp \
  /path/to/plugin/scripts/playbook-mcp.sh
```

`scripts/playbook-mcp.sh` (+ `scripts/sse_bridge.py` for team mode) need `uv` and
the repo sources; they are not used by the default SSE connection.

## Layout

```
plugins/dev-playbook/
├─ .claude-plugin/plugin.json      # only plugin.json goes in here
├─ .mcp.json                       # direct SSE to server_url, at the plugin root
├─ commands/dev-playbook-init.md   # the /dev-playbook-init command
├─ skills/using-standards/SKILL.md
├─ skills/scaffold-standards/SKILL.md
├─ hooks/hooks.json
├─ scripts/dp_init.py              # /dev-playbook-init config helper (stdlib)
├─ scripts/session_context.py      # SessionStart hook
├─ scripts/edit_gate.py            # PreToolUse hook
├─ scripts/playbook_db.py          # shared stdlib DB + marker reader for the hooks
├─ scripts/playbook-mcp.sh         # contributors: run from source
└─ scripts/sse_bridge.py           # contributors: stdio <-> SSE bridge
```

The plugin versions independently of the server: `0.3.0` here, `2.0.0` for the
MCP server in `mcp/`.
