---
title: Workflow - Code review - {{project}}
description: How to review a change in {{project}}, and how to get yours reviewed.
tags: [workflow, code-review]
id: code-review
required: false
triggers: [review, code review, pull request, PR]
---

# Workflow - Code review - {{project}}

## Triggers

| Phrase |
| --- |
| review |
| code review |
| pull request |
| PR |

## Before you start

Re-read `core/guardrails.md`. If requirements are ambiguous, ask before writing code.

## Steps

## As the reviewer

1. **Respond within one business day.** Reviewing is the work, not an interruption to it.
2. **Check in this order**: does it do the right thing, is it as simple as it could be,
   is it tested, does it fit `ARCHITECTURE.md`.
3. **Approve when it improves the codebase**, not when it is perfect. Perfect blocks
   everything.
4. **Distinguish blocking from optional.** Prefix a suggestion you are happy to lose
   with "nit:".
5. **Comment on the code, not the author.** Ask what you do not understand rather than
   assuming it is wrong.

## As the author

1. **Keep it small.** Around 100 lines reviews well; 1000 does not.
2. **Explain the intent** in the description: what, why, and how you verified it.
3. **Review your own diff first.** Half the comments you would receive, you can find.
4. **Do not argue from effort.** How long it took is not evidence that it is right.

## Done

Every applicable box in `core/definition-of-done.md` is checked, and `core/git.md`
has been followed for the commit and review.
