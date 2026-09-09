---
title: TypeScript testing - {{project}}
description: How {{project}} is tested - scope, structure, and what a good test looks like.
tags: [typescript, testing]
---

# TypeScript testing - {{project}}

Runner: **Vitest** (Jest is equivalent for everything below). Tests live beside
the code as `*.test.ts`.

## What to test

| Layer | Test it? | Why |
| --- | --- | --- |
| `domain/` | Always | Pure functions, cheapest and most valuable to cover |
| `application/` | Always | Use case orchestration and error mapping |
| `adapters/inbound/` | Yes | Validation, status codes, response shape |
| `adapters/outbound/` | Yes | Query correctness, against a real engine where possible |
| `config/` | Rarely | Wiring is verified by the tests above passing |

## Structure

Arrange, act, assert - with a blank line between the three. One behaviour per test.

```ts
it("rejects an expired card", async () => {
  const card = expiredCard();

  const result = await submitPayment(card, money("10.00"));

  expect(result).toEqual({ kind: "declined", reason: "expired_card" });
});
```

## Rules

- **Name the behaviour.** `"rejects an expired card"`, not `"test submit 2"`.
- **Prefer fakes to mocks.** A small in-memory implementation of a port reads
  better and breaks less than a stack of mocked calls. Reserve mocks for
  verifying an interaction genuinely happened.
- **No logic in tests.** A conditional in a test means it is testing two things,
  or reimplementing the code under test.
- **Deterministic.** Fake the clock, seed randomness, never hit the real network,
  never `setTimeout` to wait for something.
- **Independent.** Tests pass in any order and in parallel; no shared mutable
  module state between them.
- **Assert on outcomes, not internals.** Asserting that a private helper was
  called freezes the implementation.
- **A bug fix starts with a failing test** that reproduces the bug.

## Type-level expectations

Where a type is the contract, assert on it - a compile-time check via
`expectTypeOf` is a real test, and it fails at build time rather than in production.
