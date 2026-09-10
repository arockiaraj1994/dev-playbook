---
name: scaffold-standards
description: Create a dev-playbook standards project for a repo that has none - detect its languages, pick the template packs, fill the placeholders from the actual source, preview, and write. Use when playbook_start_task reports an unknown project, or when the user asks to set up, bootstrap or scaffold coding standards.
---

# Scaffolding standards for this repo

Creates a standards project from the base pack plus one language pack per
language in the repo: guardrails, definition of done, architecture and git
rules, task workflows, and per-language standards, anti-patterns and testing
docs.

This tool **writes**, and it is additive but not idempotent - it creates a
project and refuses to merge into an existing one. So: preview, show the user,
get their agreement, then write. Never write on the first call.

## 1. Name the project

The project name is the **basename of the workspace directory** - the same name
every later tool call will pass. Do not ask the user to invent one and do not
shorten it.

## 2. Detect the languages from the tree

Look at the actual repo, do not ask first. Build markers are the reliable
signal:

| Language | Look for |
|---|---|
| `java` | `pom.xml`, `build.gradle`, `src/main/java/` |
| `kotlin` | `build.gradle.kts`, `src/main/kotlin/`, `*.kt` |
| `python` | `pyproject.toml`, `setup.py`, `requirements.txt` |
| `typescript` | `tsconfig.json`, `package.json` with TS deps |
| `go` | `go.mod` |
| `rust` | `Cargo.toml` |

A repo can have several; pass them all. Ignore languages that appear only in
build scripts or CI config - a `.py` hook in a Java repo does not make it a
Python project.

## 3. Read the catalog

Call `mcp__plugin_dev-playbook_dev-playbook__playbook_list_templates()` to see
which packs exist and what each one requires. Pass a `language` to get the
detail for one pack. Only scaffold with languages the catalog actually offers.

## 4. Fill the placeholders from the source, not from the user

Each pack declares required placeholders. **Read the real value out of the
repo** - that is the whole point; a standards doc that says
`com.example.myapp` teaches nothing.

| Placeholder | Pack | Where the value actually is |
|---|---|---|
| `project` | base | the workspace directory basename |
| `package` | java, kotlin | the root package under `src/main/java/` or `src/main/kotlin/`, or the `group` in the build file |
| `python_package` | python | `[project] name` in `pyproject.toml`, or the top-level importable package directory |
| `module` | go | the `module` line in `go.mod` |
| `crate` | rust | `[package] name` in `Cargo.toml` |

If a value genuinely is not in the repo, ask the user for that one value. Do
not guess and do not leave the placeholder in.

## 5. Preview, then ask

```
playbook_scaffold_standards(project="<dir basename>", languages=[...],
                            placeholders={...}, dry_run=true)
```

Show the user the manifest it returns - the documents that would be created and
the values filled in. Ask whether to write it. If they want fewer rules or
workflows, `rule_ids` and `workflow_ids` narrow the selection; omitting them
takes the pack defaults.

## 6. Write it

Re-run the identical call with `dry_run=false` once they agree. Then confirm it
took:

```
playbook_start_task(project="<dir basename>", intent="<whatever is next>")
```

The SessionStart hook picks the new project up from the next session onwards,
so the guardrails arrive in context without anyone asking for them.

## If it refuses

- **Project already exists** - the store never merges. Either the standards are
  already there (read them with `playbook_find_standards`), or the user wants a
  different project name.
- **Scaffolding is disabled** - `[enable] scaffold = false` on a shared server.
  The read tools still work; an admin has to enable it or author from the
  dashboard.
- **Requires an admin token** - a shared server with auth on. The user needs an
  admin token, or an admin scaffolds from the dashboard.
