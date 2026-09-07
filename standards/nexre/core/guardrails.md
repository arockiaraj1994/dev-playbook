---
title: Guardrails - NexRe
description: Always-on MUST / MUST NOT rules. Loaded via playbook_get_doc(kind="guardrails") on every task.
tags: [guardrails, security, scope, android, kotlin]
---

# Guardrails - NexRe

These rules are non-negotiable. Re-read them at the start of every task and again before claiming done.

## MUST

- **Scope.** Change ONLY what is asked. One task = one change. No incidental refactors or "while I'm here" cleanups.
- **Read before write.** Inspect the existing file before editing. Match prevailing naming, patterns, and conventions.
- **Ask, don't guess.** If requirements are ambiguous - especially around DB schema, Gemini prompts, or WorkManager contracts - ask first.
- **Honesty.** If a proposed approach has a flaw, say so. If you don't know, say "I don't know."
- **Layer separation.** Keep `domain/` free of Android/framework imports. Keep `ui/` free of `data/` imports.
- **Hilt everywhere.** All dependencies are injected via Hilt - no manually constructed singletons.
- **Flow for live queries.** All Room queries that return observable data must return `Flow<...>`, never `LiveData`.
- **FlowRow for tags.** Chip/tag collections in Compose use `FlowRow`, not `Row`, to handle overflow.
- **EncryptedSharedPreferences for the API key.** Never store or log the Gemini key in plaintext.
- **Room migrations.** If you add or rename a column or table, you MUST add a Room `Migration` object and bump `version` in `NexReDatabase`. Flag this before coding.
- **Privacy.** User data is local-only. No network calls beyond Gemini (opt-in) and OG fetch for the URL being saved.

## MUST NOT

- **No hardcoded secrets.** API keys, tokens, and credentials must never appear in source files.
- **No PII in logs.** Don't log link content, personal notes, summaries, or the Gemini API key.
- **No `data/` imports in `ui/`.** ViewModels interact with domain use cases and repository interfaces only.
- **No `Android.*` imports in `domain/`.** Domain layer is pure Kotlin.
- **No `LiveData`.** Use `StateFlow` / `Flow` throughout.
- **No global `object` singletons** for business logic - use Hilt `@Singleton`.
- **No synchronous Room calls on the main thread.** All DAO calls are `suspend` or return `Flow`.
- **No plain `Row` for tag chips.** Always use `FlowRow` to avoid vertical overflow.
- **No schema-breaking changes without a migration.** Changing `tableName`, `columnName`, or dropping a column without a migration causes a crash on upgrade.
- **No analytics or crash-reporting SDKs** - they violate NexRe's local-only data promise.

## Definition of Done check

Before claiming a task complete, run: `bash gates/scripts/verify-kotlin.sh`
See `core/definition-of-done.md` for the full checklist.
