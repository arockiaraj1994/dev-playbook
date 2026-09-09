---
title: Workflow - Security fix - {{project}}
description: Flow for addressing a vulnerability or exposure in {{project}}.
tags: [workflow, security-fix]
id: security-fix
required: true
triggers: [security fix, vulnerability, CVE, secret leak, exposed]
---

# Workflow - Security fix - {{project}}

## Triggers

| Phrase |
| --- |
| security fix |
| vulnerability |
| CVE |
| secret leak |
| exposed |

## Before you start

Re-read `core/guardrails.md`. If requirements are ambiguous, ask before writing code.

## Steps

1. **Assess the exposure.** What data or capability is reachable, and by whom?
2. **If a secret leaked, rotate it first.** Removing it from the code does not
   un-leak it - anything committed must be treated as compromised.
3. **Fix the cause**, not the symptom: validate the input, fix the authorization
   check, upgrade the dependency.
4. **Add a regression test** that fails against the vulnerable behaviour.
5. **Check for the same pattern elsewhere** in the codebase.
6. **Run the gates.**
7. **Confirm nothing sensitive is logged** by the new code path.

## Done

Every applicable box in `core/definition-of-done.md` is checked, and `core/git.md`
has been followed for the commit and review.
