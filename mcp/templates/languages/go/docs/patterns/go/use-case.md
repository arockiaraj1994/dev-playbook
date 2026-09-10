---
title: Use case pattern - {{project}}
description: How application-layer use cases are structured in {{project}}.
tags: [pattern, use-case, go]
---

# Pattern: Use case - {{project}}

**Use this when:** adding a business operation that orchestrates domain logic and
outbound dependencies.

## Structure

```
internal/application/submit_payment.go       <- the use case and the interfaces it needs
internal/adapters/outbound/acme_gateway.go   <- satisfies one of them
cmd/api/main.go                              <- constructs both and joins them
```

## Canonical example

```go
package application

// Declared here because this is where it is consumed.
type PaymentGateway interface {
	Charge(ctx context.Context, p domain.Payment) (domain.ChargeOutcome, error)
}

type SubmitPayment struct {
	gateway  PaymentGateway
	payments PaymentRepository
}

func NewSubmitPayment(g PaymentGateway, r PaymentRepository) *SubmitPayment {
	return &SubmitPayment{gateway: g, payments: r}
}

func (s *SubmitPayment) Do(ctx context.Context, acc domain.AccountID, amt domain.Money) (domain.Result, error) {
	payment := domain.InitiatePayment(acc, amt) // domain decides

	outcome, err := s.gateway.Charge(ctx, payment) // interface does I/O
	if err != nil {
		return domain.Result{}, fmt.Errorf("charge %s: %w", payment.ID, err)
	}

	if err := s.payments.Save(ctx, domain.CompleteWith(payment, outcome)); err != nil {
		return domain.Result{}, fmt.Errorf("save %s: %w", payment.ID, err)
	}

	return domain.ResultOf(outcome), nil
}
```

## Key rules

- The interface is declared in the **application** package, next to the code that
  calls it. The adapter package imports the domain and satisfies the interface
  implicitly - it never imports the application to prove it.
- Dependencies arrive through the constructor. Only `cmd/` builds concrete
  implementations.
- `ctx` is the first parameter and is passed through every call that can block.
- Wrap every returned error with the operation and the identity that failed.
- Return domain types. Never return a database row or a transport struct.
- The use case is the transaction boundary, not the handler and not the repository.
