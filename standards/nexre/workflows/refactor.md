---
title: Workflow - Refactor
description: Flow for refactoring NexRe code without changing behavior.
triggers: [refactor, clean up, extract, move code, reorganize, rename]
gates: [verify-kotlin]
see_also: [language:kotlin/standards, language:kotlin/anti-patterns, core:guardrails]
---

# Workflow - Refactor (NexRe)

## Before you start

1. Call `playbook_get_doc(project="nexre", kind="guardrails")` - scope control is critical for refactors.
2. **Define what changes and what stays.** State this explicitly before touching any code.
3. Refactors must be behavior-preserving - if you find a bug while refactoring, fix it in a separate change.

## Common refactor types

### Extract a shared Composable
- Move the composable to `ui/components/<Name>.kt`.
- The component must have no ViewModel dependency - accept all data as parameters.
- Verify the screens that use it still compile and look identical.

### Extract a helper function from a use case or repository
- Keep the extracted function in the same file unless it's genuinely reusable.
- Don't prematurely abstract - if only one caller exists, `private fun` in the same file is enough.

### Rename a class or file
- Use IDE rename refactor (Shift+F6 in Android Studio) to catch all references.
- Rename the file to match the class name.
- Check `NexReNavHost` if the renamed class is a screen.
- If a renamed entity field changes the DB column name - stop. That's a DB migration, not a refactor.

### Move code between packages
- Domain model classes (`Link`, `Tag`, enums) must stay in `domain/model/`.
- Repository impls must stay in `data/repository/`.
- Never move a class into a layer that would create a prohibited dependency (e.g., moving a DAO into `domain/`).

### Simplify a ViewModel
- Replace `MutableStateFlow` + manual updates with `repository.flow.stateIn(...)` where appropriate.
- Verify state collection in the screen still works after the change.

## Steps

1. State the exact scope: which files, which functions.
2. Make the change.
3. Run `./gradlew assembleDebug` - fix any compilation errors.
4. Run `bash gates/scripts/verify-kotlin.sh`.
5. Verify manually: open each affected screen and exercise the refactored code path.
6. Confirm no behavioral change in the share-sheet flows (they're easy to miss).

## Done

All boxes in `core/definition-of-done.md` are checked.
