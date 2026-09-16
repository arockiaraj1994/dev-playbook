---
name: dev-playbook-init
description: Set up dev-playbook for this repo - connect the MCP server, check/create its standards project, update the global CLAUDE.md, and turn on the edit gate.
argument-hint: "[server-url]"
---

# Set up dev-playbook for this repository

You are running `/dev-playbook-init`. Configure dev-playbook for the current repo, in order, then
report what you did. The helper below writes outside the repo (global CLAUDE.md, user MCP config,
marker files) - it runs through the normal permission prompts, so let the user approve them.

## 0. Inputs
- **project** = the basename of the current working directory (e.g. `/home/me/code/billing-api` →
  `billing-api`). dev-playbook keys every standard by this name.
- **server URL** = `$ARGUMENTS` if the user passed one, otherwise the default
  `http://localhost:8420/sse` - a dev-playbook server the user runs locally via Docker. If they gave
  a bare host like `http://localhost:8420`, append `/sse`.

## 1. Configure MCP + CLAUDE.md + edit gate
Run the helper (it registers the MCP at user scope, updates the global CLAUDE.md idempotently, and
arms the edit gate):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dp_init.py" --server-url "<server-url>"
```

Add `--token "<token>"` only for a shared/team server that has auth on. Show the user the JSON
summary. If it reports the `claude` CLI is missing, relay the one-line command it prints so they can
register the MCP themselves.

The plugin also bundles the MCP over SSE, so the tools work immediately; the user-scope entry this
wrote makes `dev-playbook` appear in `claude mcp list` and in other projects. If the playbook tools
now show up twice, that is the two registrations - the user can drop the user-scope one with
`claude mcp remove --scope user dev-playbook`.

## 2. Verify the server is reachable
Call `playbook_find_standards(project="<project>")` with no query.
- **Not connected / tools not loaded** → tell the user to (a) start the server: `docker compose up -d`
  in the dev-playbook repo (serves port 8420), and (b) restart Claude Code so the freshly-registered
  MCP loads, then re-run `/dev-playbook-init`. Stop here.
- **Returns the project's document list** → the project already exists. Skip to step 4.
- **Returns "no standards project named ..." steering** → it does not exist yet. Go to step 3.

## 3. Create the standards project (only if missing)
Follow the `scaffold-standards` skill: detect the repo's languages from build markers, call
`playbook_list_templates()`, fill placeholders from the real source, preview with
`playbook_scaffold_standards(..., dry_run=true)`, show the user the manifest, and - only once they
agree - run it again with `dry_run=false`. Never invent a project for a repo the user does not want
standards on.

## 4. Mark the repo configured (lets edits through here)
Once the project exists, record it so the edit-gate hook stops blocking edits in this repo:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dp_init.py" --configured "<project>"
```

## 5. Summary
Report: the server URL; whether the project existed or was created; that the global CLAUDE.md now
points every session at the standards; and that edits are now **gated in any repo that has not been
configured** - run `/dev-playbook-init` there too. Remind them a Claude Code **restart** is needed
the first time for the user-scope MCP to load.
