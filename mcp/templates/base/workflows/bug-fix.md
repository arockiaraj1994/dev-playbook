---
title: Workflow - Bug fix - {{project}}
description: Step-by-step flow for diagnosing and fixing a bug in {{project}}.
tags: [workflow, bug-fix]
id: bug-fix
required: true
triggers: [fix bug, broken, crash, not working, regression]
---

# Workflow - Bug fix - {{project}}

## Triggers

| Phrase |
| --- |
| fix bug |
| broken |
| crash |
| not working |
| regression |

## Before you start

Re-read `core/guardrails.md`. If requirements are ambiguous, ask before writing code.

## Steps

1. **Reproduce it first.** Do not start editing until you can trigger the bug on demand.
2. **Write a failing test** that captures the bug. That test is the definition of the fix.
3. **Find the root cause.** Trace back from the symptom; do not patch where it surfaces.
4. **Fix the cause**, with the smallest change that makes the failing test pass.
5. **Check for siblings.** If the same mistake appears elsewhere, note it - fix it
   separately unless it is genuinely the same root cause.
6. **Run the gates.**
7. **Confirm no regression** in the surrounding behaviour.

## Done

Every applicable box in `core/definition-of-done.md` is checked, and `core/git.md`
has been followed for the commit and review.
