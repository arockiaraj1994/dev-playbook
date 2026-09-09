---
title: Workflow - Incident response - {{project}}
description: Flow for responding to a production incident in {{project}}.
tags: [workflow, incident-response]
id: incident-response
required: false
triggers: [incident, outage, postmortem, on call]
---

# Workflow - Incident response - {{project}}

## Triggers

| Phrase |
| --- |
| incident |
| outage |
| postmortem |
| on call |

## Before you start

Re-read `core/guardrails.md`. If requirements are ambiguous, ask before writing code.

## Steps

1. **Declare it.** An unclear incident with an owner beats a clear one without.
2. **Mitigate before diagnosing.** Roll back, disable the flag, shed load - restore
   service first, understand it second.
3. **Keep a timeline as you go.** Memory reconstructs badly afterwards.
4. **Communicate on a cadence**, even when the update is "still investigating".
5. **Once stable, find the root cause** - and follow the security-fix workflow if any
   data or credential was exposed.
6. **Write the review blamelessly.** People act reasonably given what they knew; the
   question is what made the wrong action easy.
7. **Turn findings into work items**, and make at least one of them a detection
   improvement.

## Done

Every applicable box in `core/definition-of-done.md` is checked, and `core/git.md`
has been followed for the commit and review.
