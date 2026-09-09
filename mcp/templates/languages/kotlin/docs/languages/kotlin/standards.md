---
title: Kotlin standards - {{project}}
description: Coding standards for Kotlin in {{project}}, based on the official Kotlin coding conventions.
tags: [kotlin, standards, style]
---

# Kotlin standards - {{project}}

Baseline: **Kotlin 2.1**, root package `{{package}}`. Where this document is
silent, the [official Kotlin coding
conventions](https://kotlinlang.org/docs/coding-conventions.html) apply.

## Formatting

| Rule | Value |
| --- | --- |
| Indentation | 4 spaces, never tabs |
| Column limit | 120 characters |
| Trailing commas | Allowed and encouraged in multi-line lists |
| Braces | Always for multi-line blocks; expression bodies for one-liners |
| Imports | No wildcard imports; no unused imports |

## Naming

| Thing | Convention | Example |
| --- | --- | --- |
| Package | lowercase, no underscores | `{{package}}.domain` |
| Class / interface | UpperCamelCase noun | `PaymentRequest` |
| Function | lowerCamelCase verb phrase | `submitPayment` |
| Property | lowerCamelCase noun | `accountId` |
| Constant (`const val`, top-level) | UPPER_SNAKE_CASE | `MAX_RETRIES` |
| Test function | describes the behaviour | `` `rejects expired card` `` |
| Type parameter | Single capital or descriptive | `T`, `Response` |

Do not suffix implementations with `Impl` unless there is genuinely one
implementation and no better name exists.

## Language use

- Prefer `val`; use `var` only where mutation is required.
- Prefer expression bodies for functions that are a single expression.
- Use `data class` for value carriers **without invariants**, and a regular class
  when construction must be controlled - `copy` bypasses your constructor.
- Use `sealed interface` for a closed set of variants, and `when` as an
  expression so exhaustiveness is checked.
- Use value classes for identifiers and units so the compiler distinguishes them.
- Prefer standard-library functions (`map`, `filter`, `fold`, `groupBy`) over
  manual loops where they read more clearly.
- Use scope functions deliberately: `let` for nullable chaining, `apply` for
  configuration, `require`/`check` for preconditions. Do not nest them.

## Nullability

- Let the type carry optionality; handle it with `?.`, `?:` or a narrowing check.
- Never use `!!`. Use `requireNotNull(x) { "message" }` when absence is a
  programming error and you want the message.
- Annotate or immediately convert platform types arriving from Java interop.

## Coroutines

- Every coroutine is launched in a scope with a defined lifetime.
- Suspend functions are main-safe: they switch dispatcher internally with
  `withContext` rather than requiring the caller to know.
- Inject `CoroutineDispatcher` so tests can substitute a deterministic one.
- Expose streams as `Flow`; expose one-shot work as `suspend`.
- Never catch `CancellationException` without rethrowing it.

## Errors

- Throw the most specific exception that fits; define a domain exception when no
  standard one describes the failure.
- Use `Result` or a sealed result type for expected, recoverable outcomes.
- Include the context that makes a failure diagnosable - the id, not the object.

## Dependencies

- Declare versions in one place; never inline a version at the point of use.
- Prefer the standard library. Add a dependency only when it earns its keep.
