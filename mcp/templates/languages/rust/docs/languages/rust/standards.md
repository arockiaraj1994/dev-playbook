---
title: Rust standards - {{project}}
description: Coding standards for Rust in {{project}}, based on the Rust API Guidelines and clippy pedantic.
tags: [rust, standards, style]
---

# Rust standards - {{project}}

Baseline: **Rust 1.85 (2021 edition or later)**, crate `{{crate}}`. Formatting is
whatever `rustfmt` produces. Lints are `clippy` with `pedantic` enabled and
warnings denied in CI.

## Tooling

| Job | Tool | Command |
| --- | --- | --- |
| Format | rustfmt | `cargo fmt --all -- --check` |
| Lint | clippy | `cargo clippy --all-targets -- -D warnings` |
| Tests | cargo test | `cargo test --all-features` |
| Docs | rustdoc | `cargo doc --no-deps` |
| Advisories | cargo-deny | `cargo deny check` |

Pin the toolchain in `rust-toolchain.toml` so every machine and CI runner lints
against the same clippy.

## Crate lints

Set the policy once, at the crate root, rather than per file:

```rust
#![forbid(unsafe_code)]                      // remove only with a written reason
#![warn(clippy::pedantic, clippy::nursery)]
#![warn(missing_docs, rust_2018_idioms)]
#![deny(clippy::unwrap_used, clippy::expect_used)]
```

Never blanket-`allow` at the crate root. An `#[allow]` sits on the smallest scope
that needs it, with a comment saying why.

## Naming

| Thing | Convention | Example |
| --- | --- | --- |
| Crate / module | lower_snake_case | `payment_gateway` |
| Type / trait / enum variant | UpperCamelCase | `PaymentRequest` |
| Function / variable / field | lower_snake_case | `submit_payment` |
| Constant / static | SCREAMING_SNAKE_CASE | `MAX_RETRIES` |
| Conversion, borrowed | `as_` | `as_str` |
| Conversion, owned | `into_` | `into_bytes` |
| Conversion, expensive | `to_` | `to_owned` |
| Error type | `Error` suffix | `ChargeError` |

## Types

- **Make illegal states unrepresentable.** An `enum` with data beats a struct of
  optional fields; private fields plus a constructor beat a validated-by-comment
  public struct.
- **Newtype every identifier.** `PaymentId(Uuid)` and `AccountId(Uuid)` cannot be
  swapped by accident; two `Uuid`s can.
- Take `&str`, `&[T]` and `impl IntoIterator` as parameters; return owned types.
- `#[must_use]` on anything whose result is the point, `#[non_exhaustive]` on
  public enums and structs that will grow.
- Derive `Debug` everywhere - and implement it by hand for anything holding a
  secret, so the token never reaches a log.

## Errors

- **Libraries use `thiserror`**, so callers get typed variants they can match on.
  **Binaries use `anyhow`**, adding context and reporting once at the top.
- `?` is the control flow. A `match` that only re-wraps is noise.
- `.context("charging payment")` at each hop makes the final report a story
  rather than one line.
- `unwrap` and `expect` are for tests and for invariants you can prove, with the
  proof in the comment. In a service, an `unwrap` is a crash an attacker can
  trigger.
- Never `panic!` on input. Validate and return an error.

## Unsafe

- `#![forbid(unsafe_code)]` unless the crate genuinely needs it. Removing it is a
  reviewed decision, recorded in `ARCHITECTURE.md`.
- Where it is needed: the smallest possible block, a `// SAFETY:` comment stating
  the invariant that makes it sound, a safe wrapper around it, and Miri in CI.

## Async

- One runtime, chosen once. Do not mix executors.
- No blocking call inside an `async fn` - use the async client or
  `spawn_blocking`.
- Every `spawn` returns a `JoinHandle` somebody awaits; hold long-lived tasks in
  a `JoinSet`.
- Never hold a `std::sync::Mutex` guard across an `.await`. Use the runtime's
  async mutex, or restructure so the lock is released first.
- Cancellation is normal: a future can be dropped at any await point, so clean-up
  belongs in `Drop`, not after the await.
