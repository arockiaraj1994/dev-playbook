---
title: Go standards - {{project}}
description: Coding standards for Go in {{project}}, based on Effective Go and the Google Go Style Guide.
tags: [go, standards, style]
---

# Go standards - {{project}}

Baseline: **Go 1.23**, module `{{module}}`. Formatting is whatever `gofmt`
produces and is never argued about. `go vet` and `staticcheck` are part of the
gate, not advisory.

## Tooling

| Job | Tool | Command |
| --- | --- | --- |
| Format | gofmt | `gofmt -l .` |
| Correctness | go vet | `go vet ./...` |
| Lint | staticcheck | `staticcheck ./...` |
| Tests | go test | `go test -race ./...` |
| Vulnerabilities | govulncheck | `govulncheck ./...` |

## Package layout

```
cmd/<binary>/main.go     composition root, flag parsing, wiring
internal/                everything the outside world may not import
pkg/                     only if something outside this module truly imports it
```

`internal/` is a compiler-enforced boundary. Start there and promote a package to
`pkg/` when an external consumer actually exists - not in anticipation of one.

## Naming

| Thing | Convention | Example |
| --- | --- | --- |
| Package | one lowercase word, no underscores | `payment` |
| File | lower_snake_case | `payment_gateway.go` |
| Exported identifier | PascalCase | `SubmitPayment` |
| Unexported identifier | camelCase | `submitPayment` |
| Interface | what it does | `PaymentCharger`, not `IPayment` |
| Error value | `Err` prefix | `ErrNotFound` |
| Error type | `Error` suffix | `ValidationError` |
| Test | `TestXxx` | `TestRejectsExpiredCard` |

The package name is part of every call site: `payment.New`, never
`payment.NewPayment`. Avoid `util`, `common`, `base` and `helpers` as package
names - they describe nothing and attract everything.

## Interfaces

- **Define interfaces where they are consumed**, not beside the implementation.
  The consumer knows the method set it needs; the producer returns a concrete
  type.
- Keep them small. One or two methods covers most real dependencies.
- Accept interfaces, return structs.
- A nil pointer inside a non-nil interface is not nil. Return `error` as `nil`
  explicitly rather than a typed nil.

## Errors

- Return errors, do not panic. `panic` is for a broken invariant that cannot
  continue, not for a bad request.
- Wrap with context using `%w`: `fmt.Errorf("charge %s: %w", id, err)`. The
  message reads as a chain and `errors.Is` still works.
- The message is lowercase and unpunctuated, because it will be wrapped by
  another one.
- Compare with `errors.Is`, extract with `errors.As`. Never compare error strings.
- Handle an error once. Logging it *and* returning it means it appears twice in
  the log with no extra information.

## Concurrency

- Every goroutine has an owner who knows when it ends. A goroutine started and
  forgotten is a leak.
- The first parameter of a blocking function is `ctx context.Context`, and it is
  passed on, never stored in a struct.
- Ship the `-race` detector in the gate; a data race that only appears in
  production is the expensive way to find it.
- Prefer `errgroup` for a set of related goroutines, so the first failure cancels
  the rest.
- A channel's owner closes it. A receiver never does.
