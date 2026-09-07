---
title: Workflow - Bug fix
description: Step-by-step flow for diagnosing and fixing a bug in NexRe.
triggers: [fix bug, bug fix, broken, crash, not working, wrong behavior, regression]
gates: [verify-kotlin]
see_also: [language:kotlin/anti-patterns, core:guardrails]
---

# Workflow - Bug fix (NexRe)

## Before you start

1. Call `playbook_get_doc(project="nexre", kind="guardrails")` - re-read the always-on rules.
2. Read `languages/kotlin/anti-patterns.md` - most bugs in this project fall into one of those categories.
3. **Understand the bug first** - don't write code until you know the root cause.

## Steps

1. **Reproduce** - identify exactly what triggers the bug. Is it UI, data, or background?

2. **Locate the root cause**
 - UI issue (visual glitch, overflow, wrong state displayed) → look in the Compose screen and ViewModel.
 - Data issue (wrong data saved/loaded) → look in the repository impl, DAO query, or entity mapping (`toLink` / `toEntity`).
 - Crash → read the stack trace carefully. Room crashes often point to missing migrations.
 - WorkManager issue → check `StoreLinkWorker`, retry logic, `NexReWorkerFactory`.

3. **Common bug checklist**
 - Tag overflow → `Row` used instead of `FlowRow` (see `anti-patterns.md`).
 - Stale UI → `StateFlow` not collected correctly, or using `.value` instead of `collectAsState()`.
 - Room crash on upgrade → missing migration after an entity change.
 - Gemini key missing → check `EncryptedSharedPreferences` path; key name must be `"gemini_api_key"`.
 - Enum crash → `valueOf()` without `runCatching` wrapper.
 - Network on main thread → suspend function called without `withContext(Dispatchers.IO)`.

4. **Fix the root cause** - change only what's needed. No scope creep.

5. **Run the gate**: `bash gates/scripts/verify-kotlin.sh`

6. **Verify** - confirm the bug is gone. Check adjacent flows for regressions (share-sheet, navigation, tag display).

## Done

All boxes in `core/definition-of-done.md` are checked.
