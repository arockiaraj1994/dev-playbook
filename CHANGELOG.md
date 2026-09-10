# Changelog

All notable changes to this repo are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The MCP server (under `mcp/`) follows [Semantic Versioning](https://semver.org/).
Tool name or schema changes bump the **minor** version (until 1.0.0); breaking
changes after 1.0.0 will bump the **major**.

## [Unreleased]

### Added - dev-playbook plugin v0.1.0 - one command instead of a setup guide

Reaching the v1.1.0 tool surface still cost a running server, a bearer token
and a hand-written `claude mcp add-json`. `mcp/dashboard/templates/setup.html`
exists because that is fiddly enough to need a three-tab guide. A Claude Code
plugin collapses it to one command, and carries two things the MCP surface
cannot: behaviour that applies before any tool is called, and hooks that put a
project's guardrails in context without the model having to think to ask.

- **stdio transport** - `uv run server.py --stdio`. Reuses the same module-scoped
  `Server` and `_initialization_options()` as the SSE path, so the five tools
  are identical across transports by construction rather than by being kept in
  sync; a subprocess test drives `initialize` → `tools/list` and asserts the two
  surfaces are the same set. There was no stdio transport before, so a plugin
  could only point at a running server, never launch one.
- **`plugins/dev-playbook/`** - the plugin: `plugin.json`, `.mcp.json`, two
  skills, two hooks, and a transport selector. Versioned independently of the
  server (`0.1.0` against the server's `1.1.0`).
- **Team mode** - setting `server_url` switches the transport selector from
  launching a local server to running `scripts/sse_bridge.py`, which pumps
  JSON-RPC between Claude Code's stdio and a shared server's SSE endpoint. It
  inspects nothing, so team mode cannot drift from what that server advertises,
  and its calls still land on the shared dashboard. Tested against a real server
  on a real socket rather than against mocked transports.
- **`.claude-plugin/marketplace.json`** - a self-hosted marketplace at the repo
  root, with a relative `./plugins/dev-playbook` source, so one repo carries
  both and there is one version to bump. A validator asserts the two agree.
- **`using-standards` skill** - the corrected form of
  `.cursor/rules/mcp-project-cwd.mdc`: `project` is the basename of the
  workspace directory, never another corpus project, never omitted.
- **`/dev-playbook:scaffold-standards`** - walks a repo with no standards:
  detect languages from the tree, read the catalog, take placeholder values out
  of the actual source (a Java pack's `package` from the real root package),
  preview with `dry_run=true`, ask, then write.
- **SessionStart hook** - injects the project's `core/guardrails.md` as context
  at session start. Guardrails that arrive after the first edit are guardrails
  that did not work.
- **PreToolUse hook** on `Write|Edit` - the definition of done, once per
  session, before the first edit. **Advisory by default**; `enforce_standards`
  switches it to denying edits in a repo with no standards. Off by default,
  because installing a plugin should not block anyone by surprise. The advisory
  context is once per session; the deny is every call, because a gate that
  closes once is not a gate.
- Both hooks are wired in **shell form**. The plan called for exec form
  (`args`) on the grounds that shell form rejects `${user_config.*}`; probing
  `claude plugin validate` showed exec form is accepted by the manifest and then
  dropped with *"entry ignored at runtime"* - the hooks would have silently
  never fired. Shell form costs nothing here because both scripts read
  `CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS` from the environment rather than
  interpolating it. `scripts/validate_plugin.py` and a test both now assert it.
- Both hooks read the standards SQLite directly with stdlib `sqlite3` - no MCP
  round-trip, no dependency on a running server - and exit 0 silently on any
  failure. A hook must never be why a session is broken. CI asserts they import
  nothing outside the stdlib.
- **`scripts/validate_plugin.py`** - stdlib manifest, layout and hook checks as
  the always-runs floor under `claude plugin validate`, plus a new `plugin` CI
  job that runs both.
- **`MCP_EDITOR`** - names the client in telemetry under `--stdio`, where there
  is no `User-Agent` to sniff.

### Fixed

- **`mcp/config.toml` shipped `password = "admin123"`.** Removed: a committed
  credential ends up in git history and in every clone, and a marketplace
  listing points strangers at this repo. The password now comes only from
  `MCP_ADMIN_PASSWORD`, the way `docker-compose.yml` already required.
  **The credential remains in git history** - rotating anything that reused it
  is a separate call.
- **`config.toml` also shipped `auth = true`** while `README.md` documented the
  default as false. Reconciled to `false` across `config.toml`, the `server.py`
  docstring and the README.
- **`.pre-commit-config.yaml` and `.github/workflows/ci.yml` were not valid
  YAML** and could not be parsed at all - CI had never run. Indentation fixed
  in both. `check-json` added (every new manifest is JSON, and a malformed one
  shows up only as a plugin that silently fails to load), and ruff now covers
  `plugins/` as well as `mcp/`.
- **`.cursor/rules/mcp-project-cwd.mdc` named three tools that no longer
  exist** - `playbook_start`, `playbook_get`, `playbook_find` were renamed in
  v1.1.0. Corrected, and its claim that `project` is required on every tool
  narrowed: `playbook_list_templates` does not take one.
- README claimed 601 tests; it was already 615 before this work, and is 662
  after.

### Added - v1.1.0 - the MCP tool surface is back, scaffolding-first

- **Five `playbook_*` tools.** `list_tools()` has returned `[]` since v1.0.0;
  it now advertises `playbook_start_task`, `playbook_get_standard`,
  `playbook_find_standards`, `playbook_list_templates` and
  `playbook_scaffold_standards`. Naming is `<verb>_<resource>` behind the
  `playbook_` namespace.
- **`playbook_scaffold_standards` is the headline.** An agent in a repo with no
  standards can generate a full set from the template packs. It contains no
  scaffolding logic of its own - it calls `scaffold_service.py`, the same
  function the dashboard wizard calls, and a test diffs the two resulting
  stores to prove they cannot drift.
- **`playbook_list_templates` is split out of it deliberately.** The pack
  catalog is discovery data; folding it into the scaffold tool's description
  would spend those tokens in every conversation. As its own tool it is a
  round-trip paid only when something actually needs bootstrapping.
- **`dry_run=true`** renders the manifest and writes nothing, so an agent can
  show the user what it would create before it creates it.
- **Every tool declares MCP annotations** (`readOnlyHint`, `destructiveHint`,
  `idempotentHint`, `openWorldHint`). Scaffolding is additive but not
  idempotent, so a client confirms before calling it. A client that sees no
  annotations is entitled to assume the worst, so none go out bare.
- **Every error carries a next move.** An unknown language lists the valid ids;
  a missing placeholder says where in the codebase to find it; an unresolvable
  ref lists the project's actual documents; scaffolding over an existing
  project says the store never merges and points at the read tools.
- **`mcp/tools/` module layout.** One module per tool, each exporting
  `DEFINITIONS` + `dispatch`; `server.py` concatenates and routes them. New
  `tools/refs.py` holds the ref grammar and `tools/common.py` the shared
  annotations, argument coercion and Next Calls renderer.

### Changed - v1.1.0

- **The `ref` grammar is the relative path**, plus aliases (`guardrails`,
  `workflow:bug-fix`, `gate:...`). The v0.8.0 grammar (`pattern:x`,
  `language:kotlin/testing`) addressed a filesystem corpus that no longer
  exists; storage is `standards_files(project, relative_path)`. A test asserts
  every ref the tools print resolves back to the row it names.
- **`playbook_start_task` composes `get.render_ref`** for its guardrails and
  workflow bodies, with a byte-identity test. Issue #380 found this exact
  duplication had crept back twice, because earlier passes merged tool *names*
  without merging their *renderers*.
- **`find` scores in Python** over `store.list_files()`. BM25 went with the
  v1.0.0 cut and a project is ~25 documents; FTS5 is the escalation if corpora
  grow, not now.
- **`metrics._LEGACY_TOOL_MAP` retargeted** onto the new names, and the v0.8/0.9
  three (`playbook_start` / `_get` / `_find`) added as sources. Recorded calls
  are history and must not be orphaned, so the map only ever grows.
- **`[enable] scaffold`** (default true) in `config.toml`. False hides the write
  tool and refuses it; the read tools are unaffected. With auth enabled,
  scaffolding also requires an admin token - with auth disabled every principal
  is `role="user"`, so an unconditional admin gate would lock the tool out of
  the default local config entirely.
- Dashboard setup page's "verify it works" steps now describe the real flow.
- README rewritten around the surface; it had described a tool-less skeleton and
  claimed 112 tests. `docker-compose.yml` image tag corrected from `0.8.0`.
- Tests: 522 → 601.

### Added - per-rule help popups in the create-standards wizard

- **Every rule row in the wizard now has a help affordance.** The picker asks a
  user to accept or reject 290 rules from one compressed sentence each; that
  sentence cannot be lengthened, because `_bullet()` writes it verbatim into the
  generated markdown. Rules now also carry `help:` (two to four sentences on what
  goes wrong without the rule) and `example:` (a do/don't pair), shown in a modal.
- **Placement is a hover-revealed glyph in a right-hand gutter**, invisible until
  the row is hovered or focused and pinned visible on touch, so 50+ rows do not
  become a wall of icons. It uses `opacity` rather than `display`, so it stays
  focusable and in the accessibility tree while invisible.
- **The icon is never dead.** With no authored help the modal falls back to the
  rule's title, severity, id, source, full body and target document.
- **Help never reaches the generated markdown.** It is authoring metadata for the
  picker only - not merely to save the agent's context, but because `source_hash`
  provenance means any change to the generated bullet invalidates every stored
  document's hash. A byte-identity test pins this.
- **Content authored for `base`, `java`, `typescript` and `python`** (161 rules).
  `go`, `kotlin` and `rust` run on the fallback and are listed as exempt in
  `_PACKS_WITHOUT_HELP`, so a coverage test stops the authored packs regressing.

### Changed - per-rule help popups

- `rule_row` in `_wizard.html` is a `<div>` with an inner `<label>` rather than a
  `<label>` wrapping everything: a `<button>` inside a `<label>` activates that
  label, so a nested help trigger would have toggled the checkbox on every click.
- The modal CSS moved from page-scoped `.sd-modal-*` in `standard_detail.css` to
  a shared `.modal-*` component in `style.css`, since the wizard does not load
  that sheet. `standard_detail.html`/`.js` updated to the new class names.
- The wizard's inline expand/collapse-all script moved into a new
  `dashboard/static/wizard_rules.js` alongside the dialog behaviour.

### Added - Python, Go and Rust template packs
- **Three new language packs** under `mcp/templates/languages/`, authored
  against `mcp/templates/TEMPLATE_SPEC.md` and matching the breadth of the
  existing java/kotlin/typescript packs (standards, testing, two patterns,
  anti-patterns, and contributions into guardrails / definition-of-done /
  architecture, plus a gate script):
  - **python** (3.12) - ruff + `mypy --strict`, `src/` layout and PEP 621,
    pytest, and the Bandit/OWASP vulnerability classes (`pickle`,
    `yaml.load`, `shell=True`, `eval`/`exec`, f-string SQL). Placeholder:
    `python_package`. Gate: format → lint → types → tests.
  - **go** (1.23) - Effective Go and the Google Go Style Guide,
    consumer-defined interfaces, `%w` error wrapping, table-driven tests.
    Placeholder: `module`. Gate: gofmt → vet → staticcheck → `test -race` →
    govulncheck.
  - **rust** (1.85) - Rust API Guidelines, `clippy::pedantic`,
    `thiserror`/`anyhow`, `#![forbid(unsafe_code)]`, async cancellation and
    lock-across-await rules. Placeholder: `crate`. Gate: fmt → clippy → test
    → doc → cargo-deny.
- The wizard's template-values step now carries a per-placeholder example
  (`python_package`, `module`, `crate` alongside `package`).
- Tests: `LANGUAGE_IDS` grows to six languages, so the combination matrix
  covers all 63 selections rather than 7; 242 → 522 tests.

### Added - dashboard standards module (post-v1.0.0)
- **Rebuilt the Standards/Projects dashboard page** as a lightweight,
  self-contained module: `mcp/standards_scanner.py` reads `standards/`
  straight off disk with no dependency on the deleted corpus/loader/BM25
  index. Adds `/dashboard/standards` routes and templates, a project detail
  view, and unit tests.
- `MCP_STANDARDS_ROOT` is back (defaults to `<repo>/standards`), and the
  Docker image bakes `standards/` back in.
- This does **not** restore any MCP tool - `playbook_start` / `playbook_get`
  / `playbook_find` remain deleted; the scanner only backs the dashboard page.
- Tests: 105 → 112.

### Removed - BREAKING - v1.0.0 (standards feature deleted from MCP)
- **The standards MCP tool surface is gone, code included.** Deleted
  `mcp/tools/` (all three `playbook_*` tools), `mcp/loader.py`,
  `mcp/corpus.py`, `mcp/search.py`, `mcp/cache.py`, `mcp/refs.py`,
  `mcp/index_render.py`, `mcp/quality.py`, `mcp/quality_rules.py`,
  `scripts/validate-rules.py`, `mcp/dev.py`, `TEMPLATE.md` and
  `CONTRIBUTING.md`. (`standards/` itself and the dashboard's corpus-health
  page were later rebuilt - see "Added" above.)
- **The MCP server advertises no tools.** `list_tools()` returns `[]`; every
  `tools/call` returns `Unknown tool`. Dispatch, timing and metrics recording
  are intact, so a new surface can be added at one place in `mcp/server.py`.
- Dashboard temporarily lost the Standards and Guide pages, the corpus-health
  scoring, the project detail view and `POST /dashboard/reload`; the Standards
  page was rebuilt (see "Added" above). Users, tools, searches, activity,
  setup, tokens and user admin remain.
- CI drops the corpus validation job; pre-commit drops the `validate-rules` hook.
- Tests: 280 → 105 (the corpus, search, quality, refs and tool suites are gone).
- Version bumped to **1.0.0**.

### Removed - BREAKING - v0.9.0 (requirements corpus dropped)
- **The second corpus is gone.** `requirements/` (PRDs, stories, authoring
  workflows), `mcp/requirement_rules.py`, the PRD/STORY templates, both
  dashboard requirement pages and the nav item, and the `validate-requirements`
  CI job are all removed. This reverts the v0.6.0 two-corpus feature; the server
  serves standards only.
- **Tool surface shrinks again:**
  - `playbook_start(project, intent)` - `mode=` and `ref=` removed. It no longer
    authors PRDs/stories or bundles a requirement.
  - `playbook_get(project, ref)` - the `req:` ref kind is removed.
  - `playbook_find(project, query?, type?)` - `corpus=`, `status=` and `prd=`
    are removed.
- **The corpus abstraction goes with it.** With one corpus, `corpus=` was a
  parameter that could only ever hold one value, so it is gone from `DocStore`
  (35 call sites), `RuleDoc`, `SearchResult`, the BM25 engine, and `CorpusSpec`
  - which also loses `cache_policy` / `ttl_seconds`. `DocStore` drops
  `find_by_id`, `stories_of`, `prd_of` and `replace_corpus`, gaining
  `replace_all`.
- **`MCP_REQUIREMENTS_ROOT` and `MCP_REQUIREMENTS_TTL` are removed**, along with
  the TTL reload poll that ran before every tool dispatch.
- **`POST /dashboard/reload` now reloads standards** instead of requirements, so
  the dashboard's reload button keeps working - edits to `standards/` no longer
  need a restart.
- **Metrics keep their history.** The `requirement_id` / `corpus` columns stay in
  the schema so pre-0.9.0 rows still read back, and `_LEGACY_TOOL_MAP` still folds
  the old tool names onto the current three. Only the write path and the
  requirement-coverage KPI are removed.
- `standards/apache-camel/` removed; `standards/nexre/` is the reference project.
- Version bumped to **0.9.0**.

### Changed - BREAKING - v0.8.0 (tool surface → 3, `ref` doc addresses)
- **Five tools → three.** `playbook_start`, `playbook_get`, `playbook_find`.
  `playbook_start_task` + `playbook_start_requirement` merge into
  `playbook_start(mode="code"|"prd"|"story")`; `playbook_search_docs` +
  `playbook_list_requirements` merge into `playbook_find` (whose `status=` and
  `prd=` filters were the only capability unique to the latter, and now apply
  to search results as well as listings).
- **`playbook_get(ref=…)` replaces `get_doc(kind=, name=, section=, depth=)`.**
  A `ref` is the string the corpus already uses in `see_also:` / `targets:`
  frontmatter - `guardrails`, `pattern:repository`, `language:kotlin/testing`,
  `req:ST-101` - so a rendered Next Call can be followed verbatim. The ~100-line
  `_format_call` switch that translated between the two vocabularies is gone,
  and the grammar now lives in one place (`mcp/refs.py`) shared by the tools and
  `scripts/validate-rules.py`.
  - `section=` folds into the ref (`language:java/testing`).
  - `depth=` is **removed**: a story always arrives with its parent PRD summary
    and a PRD with its story list, which is what `start_task(requirement=)`
    already did unconditionally while `get_doc` defaulted it off.
- **The duplication is gone, not just the tool count.** `playbook_start` now
  composes `get.render_ref()` for its guardrails and requirement blocks instead
  of re-rendering them; a test asserts the two are byte-identical so they cannot
  drift again.
- **Clean break on tool names in frontmatter.** `tool:` entries accept only
  `playbook_start`, `playbook_get`, `playbook_find`; every pre-0.8.0 alias is
  rejected by the validator rather than silently rendering nothing. Doc-kind
  aliases (`gates:`, `requirement:`, `core:`) still resolve. The two standards
  projects, the requirements project, and `TEMPLATE.md` are migrated.
- **Metrics history is preserved.** `_LEGACY_TOOL_MAP` folds every historical
  tool name onto the new three, so the dashboard keeps one row per tool across
  the rename.
- **Rule-engine dedupe.** `scripts/validate-rules.py` imports `REQUIRED_FILES` /
  `REQUIRED_WORKFLOWS` from `quality_rules` instead of redeclaring them, and
  validates `see_also:` through the same `refs.parse_ref` the server uses.
- Removed dead code: `metrics.args_to_doc_path` (no production caller, and a
  third copy of the ref→path mapping), `loader.bootstrap`, and the
  `allow_omit_for_cross_lookup` branch no caller ever passed.
- Version bumped to **0.8.0**.

### Changed - BREAKING - v0.7.0 (tool surface → 5, `playbook_` namespace)
- **All tools renamed with a `playbook_` prefix** so they cannot collide with
  other MCP servers in a multi-server editor setup: `playbook_start_task`,
  `playbook_get_doc`, `playbook_search_docs` (was `find_rules`),
  `playbook_list_requirements`, `playbook_start_requirement`.
- **`list_projects` removed (6 → 5 tools).** Every project-resolution error
  already lists the valid projects, so the tool was redundant.
- **`get_doc(doc=…)` → `playbook_get_doc(section=…)`.** A parameter named
  `doc` on a doc-fetching tool was ambiguous; it selects the language
  sub-doc (`standards` | `testing` | `anti-patterns`).
- **`project` is now required on every tool** (was optional on
  `start_task`/`get_doc`). No inference: the agent always states which
  project's rules it wants; wrong/missing values still return the teaching
  error with the valid project list.
- **Server-level `instructions`** now carry the cross-tool workflow ("call
  playbook_start_task first…"), so tool descriptions are declarative
  (when to use + inputs + returns) instead of ALL-CAPS orchestration.
- Old tool names remain valid in `see_also:`/`targets:` frontmatter and
  render as the new names; dashboard metrics queries count old and new
  names together. Fixed a stale `start_task` truncation hint that pointed
  at the removed `get_agents_md` tool.
- Version bumped to **0.7.0**.

### Added - v0.6.0 two-corpus (standards + requirements)
- **`standards/` + `requirements/` roots.** Projects moved under `standards/`;
  PRDs/stories live under `requirements/<project>/PRD-*/`.
- New tools: `list_requirements`, `start_requirement`. Fetching a PRD/story is
  `get_doc(kind="requirement")` - the requirements corpus is free once kinds
  share one tool. `start_task(requirement=)` does a server-side tree walk.
- `find_rules(corpus=)` - `standards` (default) | `requirements` | `all`.
- TTL cache for requirements (`MCP_REQUIREMENTS_TTL`), corpus env roots,
  `/dashboard/requirements`, `POST /dashboard/reload`.
- Status-aware requirement quality rules; CI split validate-standards /
  validate-requirements.
- Proof PRD: `requirements/nexre/PRD-001-offline-sync/` with 3 stories.

### Changed - BREAKING (tool surface → 6)
- **Nine `get_*` tools → one `get_doc(kind=…)`.** `get_agents_md`,
  `get_guardrails`, `get_architecture`, `get_language_rules`, `get_pattern`,
  `get_skill`, `get_workflow`, `get_gate`, and `get_requirement` are gone.
  `kind` selects the family; `name` / `doc` / `depth` carry the former
  per-tool args. Corpus is implied by kind.
- **`start_task(project?)` - project is optional.** Inferred when exactly one
  standards project exists; otherwise returns a short which-project list so
  it remains a genuine first call.
- **`list_rule_docs` + `search_rules` → `find_rules(…)`.** Omit `query` to list
  docs (including `triggers:`), pass `query` for BM25 search.
- **`get_index` removed.** Trigger map is surfaced by `find_rules` list mode.
- **`start_task` is the sole coding entry point.** `## Next Calls` now renders
  `get_doc(kind=…)` for every `see_also:` / `targets:` entry.
- Version unified to **0.6.0** (`pyproject.toml` + `SERVER_VERSION`).
- README auth section rewritten for local SQLite + pbkdf2 (was incorrectly
  documenting Keycloak).

### Fixed
- **`get_agents_md` was a dead end.** It is the only doc tool whose content had
  no `see_also:` frontmatter, so it returned no `## Next Calls` block and the
  agent's chain stopped there - which is why it dominated the call log. Both
  `AGENTS.md` files now declare `see_also: [tool:start_task, …]`.
- **`see_also` entries with an unrecognized kind were silently dropped.**
  `_format_call` knew only 6 kinds, so `core:guardrails` (3 nexre workflows) and
  `gates:README` (`standards/nexre/skills/release.md`) rendered nothing. Added the `tool:`
  and `core:` kinds and the `gates` alias, and `validate-rules.py` now fails on
  an unknown kind instead of letting it disappear.
- `get_gate(name=…)` now returns the script's first lines, as its description
  always promised (it previously returned only the path).
- `start_task` no longer collides the workflow body with the trailing `---` rule.
- Default `admin`/`admin` refused when `MCP_HOST=0.0.0.0`.
- `validate-rules.py` error list typed as `_Error` tuples.

### Added
- All tools are annotated `readOnlyHint: true` / `openWorldHint: false`,
  stamped centrally in `list_tools()` so a new tool cannot omit them.

## [0.3.0]

### Added
- **Usage dashboard** at `/dashboard/` (Starlette + Jinja2). Shows users / adoption, tool popularity + latency, search query log + zero-result queries, recent activity feed, and per-user drill-downs. Same `auth.enabled` flag gates both `/sse` and `/dashboard`.
- SQLite-backed metrics: every MCP tool call and every (user, editor) registration is recorded. New tables: `registrations`, `calls`. Database path configurable via `MCP_DB_PATH` (default `mcp/data/metrics.db`); excluded from git.
- Identity middleware (`identity.py`): when `auth.enabled=true`, validates Keycloak Bearer tokens and uses `preferred_username`. When `auth.enabled=false`, identifies callers via advisory `X-MCP-User` header (or `?user=` query param), falling back to client IP.
- New env vars: `MCP_HOST` (default `127.0.0.1`), `MCP_DB_PATH`, `MCP_INACTIVE_DAYS` (default 2 - threshold for "inactive" status).
- `GET /healthz` liveness probe.
- Editor detection from User-Agent (Claude Code, Cursor, Windsurf, Zed, VS Code).
- New tests: `test_metrics.py`, `test_identity.py`, `test_dashboard.py` (~50 new tests).

### Changed
- **Breaking: stdio transport removed.** The server is now SSE-only. Each install is a centrally hosted HTTP service that editors connect to.
- `setup-claude-code.sh` now takes an SSE URL argument and registers via `claude mcp add --transport sse`. Optional `BATON_RULES_TOKEN` env var adds `Authorization: Bearer …`.
- MCP server version bumped from `0.2.0` to `0.3.0`.
- New runtime dependency: `jinja2` (for dashboard templates). Already-required `starlette`/`uvicorn`/`httpx` now also serve the dashboard.
- `mcp/README.md` and root `README.md` rewritten for the hosted-server model.

### Removed
- `mcp_sse_asgi`'s old `KeycloakBearerAuthMiddleware` was extracted and generalized into `identity.IdentityMiddleware`.

### Added
- Root `README.md` with quickstart, repo layout, troubleshooting.
- `CONTRIBUTING.md` - author guide, pattern-vs-skill rule, naming conventions.
- `TEMPLATE.md` - copy-pasteable templates for `agents.md`, patterns, and skills.
- `LICENSE` (Apache 2.0), `.gitignore`, `.editorconfig`.
- `scripts/setup-claude-code.sh` - auto-detects absolute paths and registers the MCP server.
- `scripts/validate-rules.py` - pre-commit/CI gate for rule docs.
- `.pre-commit-config.yaml` and `.github/workflows/ci.yml` (lint + validate + test).
- `mcp/tests/` - unit tests for loader, search, and server tool handlers.
- MCP server: `list_rule_docs(project, doc_type?)` tool - agents can now discover patterns/skills without reading them.
- MCP server: `get_skill(project, skill)` tool - symmetric with `get_pattern`.
- MCP server: optional YAML frontmatter on rule docs (`title`, `description`, `tags`, `applies_to`); used to weight BM25 ranking.
- MCP server: snippets are now annotated with their parent markdown heading.
- MCP server: every tool invocation is logged at INFO with name + arguments.
- MCP server: `MCP_SNIPPET_SIZE` env var to tune snippet size.
- `baton-sso-config/`: stub `architecture.md`, `error-conventions.md`, `anti-patterns.md`, `glossary.md`, `skills/` dir.

### Changed
- MCP server version bumped from `0.1.0` to `0.2.0`.
- BM25 index now weights H1/H2/H3 headings and frontmatter `title`/`tags` 2× over body text.
- `search_rules` default `top_k` raised from 5 to 10; bounds now enforced (1 - 50).
- Better startup error message when no rule docs are loaded - lists the directories that *were* found.
- "Project not found" errors no longer dump the full project list inline; suggest `list_projects` instead.
- `mcp/pyproject.toml` declares `uvicorn`, `starlette`, `sse-starlette` explicitly (no longer relying on `mcp[cli]` transitive deps).
- `mcp/README.md` no longer claims "no environment variables" - env vars are now documented in a table.

### Removed
- **Breaking:** `get_error_conventions` MCP tool - use `get_rules(project, context="error-conventions")` instead. Agents that hardcoded the old name will need to update.

### Fixed
- Loader now warns (instead of silently skipping) when a markdown file lands in `doc_type="other"` or sits at the repo root outside any `<project>/` dir.
- Cursor config example removed unnecessary `cwd` field; the server resolves paths from `__file__`.
