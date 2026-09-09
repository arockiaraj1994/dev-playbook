---
title: Kotlin testing - {{project}}
description: How {{project}} is tested - scope, structure, and what a good test looks like.
tags: [kotlin, testing]
---

# Kotlin testing - {{project}}

Framework: **kotlin.test** on JUnit 5, with `kotlinx-coroutines-test` for
suspending code.

## What to test

| Layer | Test it? | Why |
| --- | --- | --- |
| `domain` | Always | Pure logic, cheapest and most valuable to cover |
| `application` | Always | Use case orchestration and error mapping |
| `adapter.inbound` | Yes | Request mapping, validation, status codes |
| `adapter.outbound` | Yes | Query correctness, against a real engine where possible |
| `di` | Rarely | Wiring is verified by the tests above passing |

## Structure

Arrange, act, assert - with a blank line between the three. One behaviour per test.

```kotlin
@Test
fun `rejects an expired card`() = runTest {
    val card = expiredCard()

    val result = submitPayment(card, money("10.00"))

    assertEquals(Declined(EXPIRED_CARD), result)
}
```

## Rules

- **Name the behaviour in backticks.** `` `rejects an expired card` ``, not
  `testSubmit2`.
- **Use `runTest` for suspending code.** It controls virtual time, so a delay
  costs nothing and the test stays deterministic.
- **Inject a `TestDispatcher`.** A hardcoded `Dispatchers.IO` makes a test racy;
  that is why dispatchers are constructor parameters.
- **Prefer fakes to mocks.** A small in-memory implementation of a port reads
  better and breaks less than a stack of stubbed calls. Reserve mocks for
  verifying an interaction genuinely happened.
- **No logic in tests.** A conditional means the test covers two things, or
  reimplements the code under test.
- **Deterministic.** Inject a clock, seed randomness, never hit the real network,
  never `Thread.sleep`.
- **Independent.** Tests pass in any order and in parallel; no shared mutable
  state in a `companion object`.
- **Assert on outcomes, not internals.** Asserting a private function was called
  freezes the implementation.
- **A bug fix starts with a failing test** that reproduces the bug.

## Test data

Build objects through small factory helpers with sensible defaults, so each test
states only the values it actually cares about.
