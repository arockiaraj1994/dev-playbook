---
title: Java testing - {{project}}
description: How {{project}} is tested - scope, structure, and what a good test looks like.
tags: [java, testing, junit]
---

# Java testing - {{project}}

Framework: **JUnit 5**. Assertions read as statements about behaviour, not about
implementation.

## What to test

| Layer | Test it? | Why |
| --- | --- | --- |
| `domain` | Always | Pure logic, cheapest and most valuable to cover |
| `application` | Always | Use case orchestration and error mapping |
| `adapter.in` | Yes | Request mapping, validation, status codes |
| `adapter.out` | Yes | Query correctness, against a real engine where possible |
| `config` | Rarely | Wiring is verified by the tests above passing |

## Structure

Arrange, act, assert - with a blank line between the three. One behaviour per test.

```java
@Test
void rejectsExpiredCard() {
    var card = Card.expiredOn(LocalDate.of(2020, 1, 1));

    var result = authorize.submit(card, Money.of("10.00"));

    assertThat(result).isEqualTo(Declined.EXPIRED_CARD);
}
```

## Rules

- **Name the behaviour, not the method.** `rejectsExpiredCard`, not `testSubmit2`.
- **Prefer fakes to mocks.** A hand-written in-memory implementation of a port is
  more readable and less brittle than a stack of `when(...).thenReturn(...)`.
  Reserve mocks for verifying that an interaction happened at all.
- **No logic in tests.** A loop or conditional in a test means it is testing more
  than one thing, or reimplementing the code under test.
- **Deterministic.** No reliance on wall-clock time, real network, ordering of a
  `HashMap`, or `Thread.sleep`. Inject a clock.
- **Independent.** Tests pass in any order and in parallel. No shared mutable static state.
- **A bug fix starts with a failing test** that reproduces the bug.
- **Assert on outcomes, not on how they were reached.** Testing that a private
  method was called locks the implementation in place.

## Test data

Build objects through small factory helpers with sensible defaults, so each test
states only the values it actually cares about.
