---
title: Workflow - New feature
description: Step-by-step flow for adding a new feature to NexRe.
triggers: [add feature, new feature, implement feature, build feature, add functionality]
gates: [verify-kotlin]
see_also: [skill:add-screen, skill:add-usecase, skill:add-room-column, pattern:repository, pattern:viewmodel, language:kotlin/standards]
---

# Workflow - New feature (NexRe)

## Before you start

1. Call `playbook_get_doc(project="nexre", kind="guardrails")` - re-read the always-on rules.
2. Read the relevant existing code before touching anything.
3. If the feature touches Room schema, read `skills/add-room-column.md` first.
4. If requirements are ambiguous, ask before coding.

## Steps

1. **Define the domain** - does the feature require a new `domain/model/` class, or extend an existing one?
 - If yes: add the class or field. If it's a field on `Link`, plan for a DB migration.

2. **Define the data contract** - what does the feature need from Room or the network?
 - New query → add to `LinkDao` or `TagDao`.
 - New table → new `@Entity` + DAO + `NexReDatabase` update + migration.
 - New network call → add to `GeminiApiService` or a new Retrofit interface.

3. **Add a use case** (if business logic is involved) - follow `skills/add-usecase.md`.
 - Simple CRUD pass-throughs (e.g., toggle favourite) don't need a use case - the ViewModel can call the repository directly.

4. **Add or update the repository** - follow `patterns/repository.md`.
 - Add new interface methods to the domain interface, implement in the impl, update the DAO.

5. **Add or update the ViewModel** - follow `patterns/viewmodel.md`.
 - Expose state as `StateFlow`. One-shot events (snackbars, navigation) via nullable `StateFlow<Event?>`.

6. **Add or update the screen** - follow `skill:add-screen` and `patterns/compose-screen.md`.
 - Use `FlowRow` for any tag/chip display.
 - Register the route in `NexReNavHost` if it's a new screen.

7. **Wire navigation** - add `navController.navigate(...)` calls from the calling screen.

8. **Run the gate**: `bash gates/scripts/verify-kotlin.sh`

9. **Verify manually** on a device/emulator:
 - Happy path end-to-end.
 - Back navigation.
 - Empty / error state.
 - No regressions in other screens (especially share-sheet flows).

## Done

All boxes in `core/definition-of-done.md` are checked.
