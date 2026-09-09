---
title: Workflow - Dependency upgrade - {{project}}
description: Flow for upgrading or adding a dependency in {{project}}.
tags: [workflow, dependency-upgrade]
id: dependency-upgrade
required: false
triggers: [upgrade dependency, bump version, update package, add library]
---

# Workflow - Dependency upgrade - {{project}}

## Triggers

| Phrase |
| --- |
| upgrade dependency |
| bump version |
| update package |
| add library |

## Before you start

Re-read `core/guardrails.md`. If requirements are ambiguous, ask before writing code.

## Steps

1. **Justify it.** Is this already covered by the standard library or an existing
   dependency? An unused abstraction is cheaper to delete than to maintain.
2. **Read the changelog** between your version and the target, not just the version
   numbers. Look for breaking changes and deprecations.
3. **Check the health of the project**: recent releases, open critical issues, licence
   compatibility.
4. **Upgrade one thing at a time.** A batch upgrade that breaks tells you nothing about
   which change broke it.
5. **Run the gates**, including any security audit.
6. **Look for newly deprecated APIs** in your own code and address them now.

## Done

Every applicable box in `core/definition-of-done.md` is checked, and `core/git.md`
has been followed for the commit and review.
