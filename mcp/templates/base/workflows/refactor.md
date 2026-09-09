---
title: Workflow - Refactor - {{project}}
description: Flow for changing the shape of {{project}} code without changing behaviour.
tags: [workflow, refactor]
id: refactor
required: true
triggers: [refactor, clean up, extract, move code, rename, reorganize]
---

# Workflow - Refactor - {{project}}

## Triggers

| Phrase |
| --- |
| refactor |
| clean up |
| extract |
| move code |
| rename |
| reorganize |

## Before you start

Re-read `core/guardrails.md`. If requirements are ambiguous, ask before writing code.

## Steps

1. **Confirm the tests cover the behaviour** you are about to move. If they do
   not, write those tests first - that is the safety net.
2. **Do not change behaviour and structure in the same commit.** Pick one.
3. **Move in small steps**, keeping the suite green between each.
4. **Check `ARCHITECTURE.md`** - a refactor that crosses a module boundary must
   respect the dependency direction, or update the document deliberately.
5. **Run the gates.**
6. **Confirm the diff contains no behaviour change.** If it does, split it out.

## Done

Every applicable box in `core/definition-of-done.md` is checked, and `core/git.md`
has been followed for the commit and review.
