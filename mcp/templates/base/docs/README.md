---
title: "{{project}} standards"
description: How the {{project}} standards corpus is organised and how to use it.
tags: [readme]
---

# {{project}} standards

Languages: **{{languages}}**

| Directory | Holds |
| --- | --- |
| `core/` | Guardrails, git practice, definition of done, glossary |
| `languages/` | Per-language standards, testing and anti-patterns |
| `patterns/` | Canonical shapes for recurring work, per language |
| `workflows/` | Step-by-step flows per task type |
| `gates/` | Executable verification scripts |

Start at `AGENTS.md`. It names every other document and the order they take
precedence in.

## Keeping this current

These documents describe how {{project}} is actually built. When the code and a
rule disagree, one of them is wrong - fix whichever it is, and do not leave the
contradiction in place.
