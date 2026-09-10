---
title: Rust testing - {{project}}
description: How {{project}} is tested - scope, structure, and what a good test looks like.
tags: [rust, testing]
---

# Rust testing - {{project}}

Unit tests live in a `#[cfg(test)] mod tests` beside the code they cover;
integration tests live in `tests/` and see only the public API. The suite runs
with `cargo test --all-features`.

## What to test

| Layer | Test it? | Why |
| --- | --- | --- |
| `domain/` | Always | Pure functions - cheapest and most valuable to cover |
| `application/` | Always | Use case orchestration and error mapping |
| `adapters/inbound/` | Yes | Validation, status codes, response shape |
| `adapters/outbound/` | Yes | Query correctness, against a real engine where possible |
| `main.rs` | Rarely | Wiring is verified by the tests above passing |

Put at least one test in `tests/` for anything you expect other crates to use -
it is the only place that proves the public API is usable from outside.

## Structure

Arrange, act, assert - with a blank line between the three. One behaviour per test.

```rust
#[test]
fn rejects_an_expired_card() {
    let card = expired_card();

    let result = submit_payment(&card, Money::from_minor(1_000));

    assert_eq!(result, Err(Declined::ExpiredCard));
}
```

A fallible test returns `Result`, so `?` works and a failure reads as a failure
rather than a panic:

```rust
#[test]
fn parses_a_valid_request() -> anyhow::Result<()> {
    let req: CreatePayment = serde_json::from_str(VALID_BODY)?;

    assert_eq!(req.currency, Currency::Gbp);
    Ok(())
}
```

## Rules

- **Name the behaviour.** `rejects_an_expired_card`, not `test_submit_2`.
- **`unwrap` is fine in tests** - a panic is a failure, which is what you want.
  It is still banned in `src/` outside `#[cfg(test)]`.
- **Prefer fakes to mocks.** A small struct implementing the trait reads better
  and breaks less than generated expectations.
- **No logic in tests.** A conditional means the test covers two things, or
  reimplements the code under test.
- **Deterministic.** Inject the clock, seed randomness, never hit the real
  network, never `sleep` to synchronise.
- **Independent.** `cargo test` runs tests in parallel threads by default; no
  shared mutable statics, no fixed ports, no shared temp paths.
- **Assert on outcomes, not internals.** `assert_eq!` on the returned value, not
  on which private helper ran.
- **Property tests where the input space is large.** A parser, a codec or a
  numeric routine earns a `proptest`; a fuzz target earns one for anything
  decoding untrusted bytes.
- **A bug fix starts with a failing test** that reproduces the bug.
- **Doc examples are tests.** Code in `///` runs under `cargo test`, so it cannot
  quietly rot.
