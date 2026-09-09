---
title: Use case pattern - {{project}}
description: How application-layer use cases are structured in {{project}}.
tags: [pattern, use-case, typescript]
---

# Pattern: Use case - {{project}}

**Use this when:** adding a business operation that orchestrates domain logic and
outbound ports.

## Structure

```
src/application/submit-payment.ts        <- the use case
src/application/ports/payment-gateway.ts <- outbound port it needs
src/adapters/outbound/acme-gateway.ts    <- implements the port
```

## Canonical example

```ts
import type { PaymentGateway, PaymentRepository } from "./ports";

export type SubmitResult =
  | { kind: "accepted"; id: PaymentId }
  | { kind: "declined"; reason: DeclineReason };

export function makeSubmitPayment(deps: {
  gateway: PaymentGateway;
  payments: PaymentRepository;
}) {
  return async function submitPayment(
    account: AccountId,
    amount: Money,
  ): Promise<SubmitResult> {
    const payment = initiatePayment(account, amount);   // domain decides
    const outcome = await deps.gateway.charge(payment); // port does I/O
    await deps.payments.save(completeWith(payment, outcome));

    return outcome.approved
      ? { kind: "accepted", id: payment.id }
      : { kind: "declined", reason: outcome.reason };
  };
}
```

## Key rules

- Dependencies arrive as an argument, never imported directly from an adapter.
  Only the composition root knows the concrete implementation.
- The port interface lives in `application/ports/` and is implemented in
  `adapters/outbound/`, so the dependency points inward.
- Return a discriminated union for expected outcomes. Reserve `throw` for the
  genuinely unexpected.
- Never return a storage row or a transport DTO - return domain types.
- The use case is the transaction boundary, not the handler and not the repository.
- Business rules live in the domain functions. The use case sequences work; it
  does not encode the rules.
