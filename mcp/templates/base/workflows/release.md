---
title: Workflow - Release - {{project}}
description: Flow for cutting and publishing a release of {{project}}.
tags: [workflow, release]
id: release
required: false
triggers: [release, cut a release, publish, ship a version]
---

# Workflow - Release - {{project}}

## Triggers

| Phrase |
| --- |
| release |
| cut a release |
| publish |
| ship a version |

## Before you start

Re-read `guardrails.md`. If requirements are ambiguous, ask before writing code.

## Steps

1. **Confirm the default branch is green.** Never release from a red build.
2. **Decide the version** from the commits since the last tag: a breaking change is
   major, a `feat` is minor, a `fix` is patch.
3. **Update the changelog** - Added, Changed, Fixed, Removed, Security - written for
   humans, not generated from commit subjects verbatim.
4. **Tag the release commit** with the version.
5. **Run the gates** against the tagged commit, not against your working tree.
6. **Publish**, then verify the published artifact actually installs and runs.
7. **Announce** what changed and anything that needs action from consumers.

## Done

Every applicable box in `gates/definition-of-done.md` is checked, and `git.md`
has been followed for the commit and review.
