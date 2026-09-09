---
title: TypeScript standards - {{project}}
description: Coding standards for TypeScript in {{project}}, based on typescript-eslint strict-type-checked.
tags: [typescript, standards, style]
---

# TypeScript standards - {{project}}

Baseline: **TypeScript 5.x**, source under `src/`. Lint baseline is
[`strict-type-checked`](https://typescript-eslint.io/users/configs/) plus
`stylistic-type-checked`.

## Compiler settings

These are not negotiable per-file:

| Option | Value | Why |
| --- | --- | --- |
| `strict` | `true` | The whole point of the language |
| `noUncheckedIndexedAccess` | `true` | `arr[0]` is `T \| undefined`, which is the truth |
| `exactOptionalPropertyTypes` | `true` | Distinguishes "absent" from "explicitly undefined" |
| `noImplicitOverride` | `true` | Catches accidental overrides |
| `isolatedModules` | `true` | Keeps builds tool-agnostic |

## Naming

| Thing | Convention | Example |
| --- | --- | --- |
| File | kebab-case | `payment-gateway.ts` |
| Type / interface | PascalCase | `PaymentRequest` |
| Function / variable | camelCase | `submitPayment` |
| Constant (module-level) | UPPER_SNAKE_CASE | `MAX_RETRIES` |
| Type parameter | Descriptive PascalCase | `TResponse` |
| Boolean | `is` / `has` / `can` prefix | `isExpired` |

## Types

- Prefer `type` for unions and `interface` for object shapes that may be extended;
  be consistent within a module.
- Model states as a **discriminated union**, not a bag of optional fields. Make
  illegal states unrepresentable.
- Use `readonly` on parameters and properties that must not be mutated, and
  `as const` for literal data.
- Prefer `unknown` over `any` at every boundary, then narrow with a type guard or
  a schema parse.
- Do not export a type you do not want to become part of the public contract.

## Async

- `async`/`await` throughout; no `.then()` chains mixed into the same function.
- Every promise is awaited, returned, or explicitly handled.
- Use `Promise.all` for independent work and a sequential loop for dependent work -
  the choice should be deliberate and obvious.
- Propagate cancellation with `AbortSignal` where an operation can be abandoned.

## Errors

- Throw `Error` or a subclass, never a string or a plain object.
- Attach context with `cause` rather than stringifying and rethrowing.
- Map expected failures to a result type; reserve exceptions for the unexpected.

## Modules

- Named exports only.
- Each module has an `index.ts` that defines its public surface.
- No side effects at import time.
