---
title: Workflow - Hotfix - {{project}}
description: Flow for an urgent production fix to {{project}}.
tags: [workflow, hotfix]
id: hotfix
required: false
triggers: [hotfix, urgent fix, production down, sev]
---

# Workflow - Hotfix - {{project}}

## Triggers

| Phrase |
| --- |
| hotfix |
| urgent fix |
| production down |
| sev |

## Before you start

Re-read `core/guardrails.md`. If requirements are ambiguous, ask before writing code.

## Steps

1. **Stabilise first.** If a rollback or feature flag restores service, do that
   before writing a fix.
2. **Branch from the released tag**, not from the default branch, so unreleased work
   does not ride along.
3. **Make the smallest possible change.** A hotfix is not the time for cleanup.
4. **Write the regression test.** Urgency is not a reason to skip it.
5. **Get a review** - the bar does not drop under pressure, and a second pair of eyes
   matters most when everyone is rushing.
6. **Run the gates**, then release and verify in production.
7. **Merge back** into the default branch so the fix is not lost in the next release.
8. **Write it up** afterwards: cause, detection, fix, prevention.

## Done

Every applicable box in `core/definition-of-done.md` is checked, and `core/git.md`
has been followed for the commit and review.
