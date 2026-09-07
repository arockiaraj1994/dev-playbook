# Contributing

This repo holds two things you can extend:

1. **Per-project rule docs** under `<project>/` (the bulk of contributions).
2. **The MCP server** under `mcp/` (Python).

This guide covers both. For copy-pasteable doc skeletons, see [`TEMPLATE.md`](TEMPLATE.md).

---

## Adding a new project ruleset

Create a directory next to `mcp/`. The validator enforces the full layout - 
copy from `standards/nexre/` for a known-good baseline.

```
my-project/
  README.md                          # humans
  AGENTS.md                          # required - identity + behavior
  INDEX.md                           # auto-generated trigger map (do not hand-edit)
  core/
    guardrails.md                    # required - always-on MUST / MUST NOT
    definition-of-done.md            # required - gates + functional + security
    glossary.md                      # required - domain terms
  architecture/
    overview.md                      # required - system overview
    decisions/                       # ADRs (one .md per decision)
  languages/<lang>/                  # at least one with standards.md
    standards.md
    testing.md
    anti-patterns.md
  patterns/<name>.md                 # canonical noun-named patterns
  skills/<action>.md                 # verb-noun playbooks
  workflows/                         # required - all four flows
    new-feature.md
    bug-fix.md
    security-fix.md
    refactor.md
  gates/
    README.md                        # required - what each gate enforces
    scripts/verify-<lang>.sh         # executable
```

After adding the directory:

```bash
python scripts/validate-rules.py --regen-index   # write INDEX.md
python scripts/validate-rules.py --check         # full validation + INDEX freshness
```

Then restart any running MCP server.

---

## Pattern vs Skill - the canonical rule

Authors frequently get this wrong. There is one rule:

> **Pattern** = canonical code structure. *"This is what good code in this stack looks like."* Reference material. Noun-named: `react.md`, `quarkus.md`, `keycloak-oidc.md`, `camel-sftp-route.md`.
>
> **Skill** = step-by-step task workflow. *"Do these steps in order."* Procedural. Verb-noun-named: `add-connector.md`, `register-schema.md`, `deploy-extension.md`.

If a doc tells you **what good code looks like**, it's a pattern. If it tells you **what to do, in order**, it's a skill. When in doubt: ask, don't split.

---

## File naming

- **Kebab-case** for filenames: `camel-sftp-route.md`, `register-schema.md`.
- **Patterns** are nouns (the thing): `quarkus.md`, `keycloak-oidc.md`.
- **Skills** are verb + noun (the action): `add-connector.md`, `deploy-extension.md`.
- One topic per file. If a pattern is 600 lines, split it; if it's two tightly related things, merge them.

## H1 convention

The first line of every doc is its title. The MCP server uses the H1 as the doc's display name when frontmatter is absent.

| Doc type | H1 format |
|----------|-----------|
| `AGENTS.md` | `# AGENTS.md - <Project> (<short stack>)` |
| `core/guardrails.md` | `# Guardrails - <Project>` |
| `core/definition-of-done.md` | `# Definition of Done - <Project>` |
| `architecture/overview.md` | `# Architecture - <Project>` |
| `architecture/decisions/<n>.md` | `# ADR <n> - <decision>` |
| `languages/<lang>/<doc>.md` | `# <Language> <doc> - <Project>` |
| `patterns/<name>.md` | `# Pattern: <Name> - <project context>` |
| `skills/<action>.md` | `# Skill: <Action> - <when to use>` |
| `workflows/<name>.md` | `# Workflow - <Name>` |
| `gates/README.md` | `# Gates - <Project>` |

---

## Optional YAML frontmatter

The MCP loader reads frontmatter when present and falls back to the H1 otherwise. Authors can adopt it incrementally.

```yaml
---
title: Quarkus pattern for Karavan
description: Canonical Quarkus structure - REST resources, services, CDI scopes.
tags: [quarkus, java, cdi, rest]
applies_to: [nexre]
---

# Pattern: Quarkus - Apache Camel Karavan
...
```

Recognized fields:

| Field | Type | Used for |
|-------|------|----------|
| `title` | string | Display name; weighted 2× in BM25 search. |
| `description` | string | Short summary returned by `playbook_find` and rendered into `INDEX.md`. |
| `tags` | list of strings | Weighted 2× in BM25 search. |
| `applies_to` | list of strings | Project scopes; informational for now. |
| `triggers` | list of strings | Natural-language task triggers used by `playbook_start`, `playbook_find`, and the `INDEX.md` generator. Workflows and skills should set this. |
| `see_also` | list of strings | `<kind>:<name>` entries - rendered as `## Next Calls` on tool fetches. See below. |
| `language` | string | Set automatically for `languages/<lang>/*.md`; can be set explicitly for other docs that target one language. |
| `gates` | list of strings | On workflows: which `verify-*.sh` gate(s) close out the task. |

Unknown keys are ignored. No frontmatter at all is fine, but workflows and
skills without `triggers` won't be discoverable through `playbook_start`.

### `see_also` - how a doc chains forward

Each entry is `<kind>:<name>`, and the server renders it as a literal tool
call in a `## Next Calls` block appended to the doc. **A doc with no
`see_also` is a dead end** - the agent reads it and has nowhere to go.

Since v0.8.0 the entry **is** the argument: an entry renders as
`playbook_get(project=…, ref="<the entry>")`, with no translation step.

| Kind | Example | Renders as |
|------|---------|------------|
| `tool` | `tool:playbook_start` | `playbook_start(project=…, intent=…)` |
| `pattern` | `pattern:error-handling` | `playbook_get(ref="pattern:error-handling")` |
| `skill` | `skill:debug-route` | `playbook_get(ref="skill:debug-route")` |
| `workflow` | `workflow:bug-fix` | `playbook_get(ref="workflow:bug-fix")` |
| `gate` | `gate:verify-java` | `playbook_get(ref="gate:verify-java")` |
| `language` | `language:java/standards` | `playbook_get(ref="language:java/standards")` |
| `architecture` | `architecture:0007-use-sftp` | `playbook_get(ref="architecture:0007-use-sftp")` |
| `core` | `core:guardrails` | `playbook_get(ref="guardrails")` |

`gates:` is a tolerated alias of `gate:`, and any `core:` entry resolves to the
guardrails bundle - these are doc kinds, so existing frontmatter keeps working.

Valid `tool:` names are `playbook_start`, `playbook_get`, and `playbook_find`.
**v0.8.0 is a clean break**: the v0.7.0 names (`playbook_start_task`,
`playbook_get_doc`, …) and the pre-0.7.0 unprefixed ones are no longer accepted.
Any other kind or tool name is a **validation error** - `validate-rules.py`
rejects it, because an unrecognized entry renders as nothing at all and the dead
link is otherwise invisible.

---

## Cross-referencing

- Link to **`./core/glossary.md`** the first time a domain term appears in a doc.
- When a pattern explicitly avoids an anti-pattern, link to the relevant `languages/<lang>/anti-patterns.md`.
- Use `see_also:` frontmatter to wire up the `## Next Calls` chain - this is how AI agents move from a workflow to its skills and patterns without having to guess paths.

---

## PR conventions

- **Title:** `rules(<project>): short imperative description` for rule changes; `mcp: …` for server changes.
- **Scope:** one project per PR when possible. Don't bundle a server refactor with a rule rewrite.
- **CI must pass:** ruff lint, the rule validator (`scripts/validate-rules.py --check`, which also enforces `INDEX.md` freshness), and unit tests.

---

## Working on the MCP server

```bash
cd mcp
uv sync                       # install deps + dev deps
uv run pytest                 # run tests
uv run ruff check .           # lint
uv run ruff format --check .  # check formatting (no edits)
```

The server reads rules from the parent directory of `mcp/` at startup. To test against a smaller corpus, point `MCP_CONFIG` at a `config.toml` whose layout you control, or create a minimal sandbox repo.

When you change a tool's name or schema, bump the server version in `mcp/pyproject.toml` and add an entry to [`CHANGELOG.md`](CHANGELOG.md).

---

## Style

- Prefer **imperative voice** in rule docs ("Use Bean Validation", not "You should use Bean Validation").
- No marketing language. AI agents and humans both prefer dense, scannable prose.
- Short sections with explicit headings beat long flowing prose - the BM25 index weights headings higher.
- Code blocks should be runnable or near-runnable; avoid pseudocode unless it's clearly labeled.
