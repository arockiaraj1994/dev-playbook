# Templates

Copy-pasteable starting points for the doc types in the per-project layout
under `standards/<project>/`. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for
naming, the pattern-vs-skill rule, and the H1 convention.

For PRDs and stories, use `mcp/templates/PRD.md` and `mcp/templates/STORY.md`
(or call `playbook_start(mode="prd")`) and place them under `requirements/<project>/`.

The YAML frontmatter block is optional except where noted (workflows and
skills should set `triggers:` and `see_also:` so `playbook_start` and
`INDEX.md` work).

Two conventions in `AGENTS.md` are load-bearing, not decorative:

- **`see_also: [tool:playbook_start, …]`** - without it the doc renders no
  `## Next Calls` block, so an agent that opens `AGENTS.md` first has nowhere
  to chain to and simply stops. This is the single most common authoring
  mistake.
- **A `## IDENTITY` H2** - `playbook_start` lifts exactly this section into its
  bundle. Anything outside it is only reachable via `playbook_get(ref="agents")`.

---

## `AGENTS.md`

```markdown
---
title: AGENTS.md - <Project>
description: Identity and behavior for AI agents working on <project>.
tags: [<stack>, <lang>]
see_also: [tool:playbook_start, tool:playbook_get, workflow:new-feature, workflow:bug-fix]
---

# AGENTS.md - <Project> (<short stack>)

**Stack:** <one-line stack summary>

---

## IDENTITY

You are a senior engineer working on **<project>** - <one-paragraph description
of what the codebase does and what "good" looks like>.

You write minimal, correct, production-ready code.
You value working software over clever abstractions.
You fix root causes, not symptoms.

---

## CONTEXT DOCS

| Doc | Purpose |
|-----|---------|
| `./INDEX.md` | Auto-generated trigger map |
| `./core/guardrails.md` | Always-on MUST / MUST NOT rules |
| `./core/definition-of-done.md` | Tests + lint + security gates |
| `./core/glossary.md` | Domain terms |
| `./architecture/overview.md` | System overview |
| `./architecture/decisions/` | ADRs |
| `./languages/<lang>/` | Per-language standards, testing, anti-patterns |
| `./patterns/` | Canonical code patterns |
| `./skills/` | Verb-noun playbooks |
| `./workflows/` | Task-driven flows |
| `./gates/` | Verification scripts |

**Call `playbook_start` first.** It returns guardrails + the matched workflow + next-call hints.
```

---

## `core/guardrails.md`

```markdown
---
title: Guardrails - <Project>
description: Always-on MUST / MUST NOT rules.
tags: [guardrails, security]
---

# Guardrails - <Project>

## MUST
- Scope: one task = one change.
- Read before write.
- Ask, don't guess.
- <stack-specific MUSTs>

## MUST NOT
- No hardcoded secrets.
- <stack-specific MUST NOTs>

## Definition of Done check
Run `bash gates/scripts/verify-<lang>.sh` before claiming done.
```

---

## `core/definition-of-done.md`

```markdown
---
title: Definition of Done - <Project>
description: Tests + lint + security gates that must pass.
---

# Definition of Done - <Project>

## Mechanical (run via `gates/scripts/verify-<lang>.sh`)
- [ ] <build step>
- [ ] <lint step>
- [ ] <test step>
- [ ] <security scan>

## Functional
- [ ] <feature-level checks>

## Security
- [ ] <secret hygiene, TLS, etc.>
```

---

## `architecture/overview.md`

```markdown
# Architecture - <Project>

## System overview
<paragraph>

## Modules
| Layer | Responsibility |
|---|---|
| ... | ... |

## Tech stack
<table>

## Decisions
See `architecture/decisions/` for ADRs.
```

---

## `languages/<lang>/standards.md`

```markdown
---
title: <Language> standards - <Project>
description: Coding standards for <lang> in <project>.
language: <lang>
---

# <Language> standards - <Project>

## Language baseline
- ...

## Naming
- ...

## Project structure
- ...
```

---

## `patterns/<name>.md`

```markdown
---
title: <Name> pattern - <Project>
description: <one-sentence summary>.
tags: [<stack>, <feature>]
see_also: [skill:<related-skill>]
---

# Pattern: <Name> - <project context>

**Use this when:** <one-line trigger>.

## Structure
<!-- Folder layout, file roles, key types. -->

## Canonical example
```<lang>
<minimal example>
```

## Key rules
- <do/don't 1>
- <do/don't 2>
```

---

## `skills/<action>.md`

```markdown
---
title: <Action> - <Project>
description: <one-sentence summary>.
triggers: [<phrase>, <phrase>]
see_also: [pattern:<name>, language:<lang>/standards]
---

# Skill: <Action> - <when to use>

## Steps
1. **<Step>** - <what to do>.
2. **<Step>** - <verification>.

## Constraints
- <non-obvious constraint>.
```

---

## `workflows/<name>.md`

```markdown
---
title: Workflow - <Name>
description: <one-sentence summary>.
triggers: [<phrase>, <phrase>]
gates: [verify-<lang>]
see_also: [skill:<x>, pattern:<y>, language:<lang>/standards]
---

# Workflow - <Name>

## Steps
1. ...
2. ...

## Done
- All boxes in `core/definition-of-done.md` are checked.
```

---

## `gates/README.md`

```markdown
---
title: Gates - <Project>
description: Executable verification scripts.
---

# Gates - <Project>

## verify-<lang>.sh

```
bash gates/scripts/verify-<lang>.sh
```

What it runs: <list>.
```

---

## `gates/scripts/verify-<lang>.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. <build / typecheck>
# 2. <lint / format>
# 3. <tests>
# 4. <security scan>

echo "OK"
```

Mark executable: `chmod +x gates/scripts/verify-<lang>.sh`. The validator fails if it isn't.
