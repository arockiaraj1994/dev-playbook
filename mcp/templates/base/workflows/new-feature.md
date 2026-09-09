---
title: Workflow - New feature - {{project}}
description: Step-by-step flow for adding a feature to {{project}}.
tags: [workflow, new-feature]
id: new-feature
required: true
triggers: [add feature, new feature, implement feature, build feature]
---

# Workflow - New feature - {{project}}

## Triggers

| Phrase |
| --- |
| add feature |
| new feature |
| implement feature |
| build feature |

## Before you start

Re-read `core/guardrails.md`. If requirements are ambiguous, ask before writing code.

## Steps

1. **Re-read the guardrails.** `core/guardrails.md`, then the anti-patterns
   document for the language you are about to touch.
2. **Read the existing code** in that area. Match what is already there.
3. **Check `ARCHITECTURE.md`** before creating any file, so it lands in the right module.
4. **Model the domain first.** Add or extend the domain type before wiring anything to it.
5. **Add the use case**, then the port it needs, then the adapter that implements the port.
6. **Write the test alongside**, not after. It must fail before the change and pass after.
7. **Wire it up** at the composition root.
8. **Run the gates.**
9. **Verify manually**: happy path, empty state, and one error case.

## Done

Every applicable box in `core/definition-of-done.md` is checked, and `core/git.md`
has been followed for the commit and review.
