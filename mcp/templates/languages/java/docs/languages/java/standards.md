---
title: Java standards - {{project}}
description: Coding standards for Java in {{project}}, based on the Google Java Style Guide.
tags: [java, standards, style]
---

# Java standards - {{project}}

Baseline: **Java 21**, root package `{{package}}`. Where this document is silent,
the [Google Java Style Guide](https://google.github.io/styleguide/javaguide.html)
applies.

## Formatting

| Rule | Value |
| --- | --- |
| Indentation | 2 spaces, never tabs |
| Column limit | 100 characters |
| Source encoding | UTF-8 |
| Braces | Always, even for single-statement blocks |
| Import order | No wildcard imports; no unused imports |

One top-level class per file, named exactly as the file.

## Naming

| Thing | Convention | Example |
| --- | --- | --- |
| Package | lowercase, no underscores | `{{package}}.domain` |
| Class / interface | UpperCamelCase noun | `PaymentRequest` |
| Method | lowerCamelCase verb phrase | `submitPayment` |
| Constant | UPPER_SNAKE_CASE | `MAX_RETRIES` |
| Field / parameter | lowerCamelCase | `accountId` |
| Type parameter | Single capital or short name | `T`, `RESP` |
| Test method | describes the behaviour | `rejectsExpiredCard` |

Do not prefix interfaces with `I` or suffix implementations with `Impl` unless
there is genuinely only one implementation and no better name exists.

## Language use

- Prefer immutability: `final` fields, `record` for data carriers, unmodifiable
  collections returned from getters.
- Use `Optional` for an absent return value. Never for a field, a parameter, or a
  collection element.
- Prefer `var` only where the initializer makes the type obvious at a glance.
- Use enhanced `switch` expressions and pattern matching over chained `instanceof`.
- Use sealed interfaces to model a closed set of variants, so the compiler checks
  exhaustiveness.
- Prefer `java.time` over legacy date classes, and `BigDecimal` for money.

## Errors

- Throw the most specific exception that fits; define a domain exception when no
  standard one describes the failure.
- Include the context that makes the failure diagnosable - the id, not the whole object.
- Use unchecked exceptions for programming errors and checked ones only where the
  caller can genuinely recover.
- Never catch an exception you cannot handle just to log and rethrow the same thing.

## Concurrency

- Prefer immutable objects and confined state over locking.
- Use `java.util.concurrent` types rather than `wait`/`notify`.
- Use a bounded executor or virtual threads; never create threads per unit of work.
- Document the threading expectations of any type that is not obviously thread-safe.

## Dependencies

- Declare versions in one place; never inline a version at the point of use.
- Prefer the standard library. Add a dependency only when it earns its maintenance cost.
