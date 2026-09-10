---
title: Go testing - {{project}}
description: How {{project}} is tested - scope, structure, and what a good test looks like.
tags: [go, testing]
---

# Go testing - {{project}}

Tests live beside the code as `*_test.go`, in the same package for unit tests and
in `package x_test` when the test should see only the public surface. The suite
runs with `go test -race -count=1 ./...`.

## What to test

| Layer | Test it? | Why |
| --- | --- | --- |
| `internal/domain/` | Always | Pure functions - cheapest and most valuable to cover |
| `internal/application/` | Always | Use case orchestration and error mapping |
| `internal/adapters/inbound/` | Yes | Validation, status codes, response shape |
| `internal/adapters/outbound/` | Yes | Query correctness, against a real engine where possible |
| `cmd/` | Rarely | Wiring is verified by the tests above passing |

## Structure

Table-driven by default. One row per case, one behaviour per test function:

```go
func TestSubmitPayment(t *testing.T) {
	tests := map[string]struct {
		card Card
		want Result
	}{
		"expired card is declined": {card: expiredCard(), want: Declined("expired_card")},
		"valid card is accepted":   {card: validCard(), want: Accepted(paymentID)},
	}

	for name, tc := range tests {
		t.Run(name, func(t *testing.T) {
			t.Parallel()

			got := SubmitPayment(tc.card, money("10.00"))

			if got != tc.want {
				t.Errorf("got %v, want %v", got, tc.want)
			}
		})
	}
}
```

## Rules

- **Name the behaviour** in the subtest name, so a failure reads as a sentence.
- **Keep the logic in the loop, not in the row.** A table field that switches
  behaviour means these are two tests.
- **Prefer fakes to mocks.** A small struct implementing the interface reads
  better than a generated mock with expectations.
- **`t.Helper()` in every assertion helper**, so the failure points at the test.
- **Clean up with `t.Cleanup`**, not a deferred close that a `t.Fatal` skips.
- **`t.Parallel()` where it is safe**, and never share a mutable fixture between
  parallel subtests.
- **No `time.Sleep` to synchronise.** Wait on a channel or a `context` deadline.
- **Assert on outcomes, not internals.** Use `cmp.Diff` for structs so the
  failure shows what differed.
- **A bug fix starts with a failing test** that reproduces the bug.
- **Fuzz the parsers.** Anything that decodes bytes from outside earns a
  `FuzzXxx` target.
