---
title: Use case pattern - {{project}}
description: How application-layer use cases are structured in {{project}}.
tags: [pattern, use-case, python]
---

# Pattern: Use case - {{project}}

**Use this when:** adding a business operation that orchestrates domain logic and
outbound ports.

## Structure

```
src/{{python_package}}/application/submit_payment.py        <- the use case
src/{{python_package}}/application/ports.py                 <- the ports it needs
src/{{python_package}}/adapters/outbound/acme_gateway.py    <- implements a port
```

## Canonical example

```python
from dataclasses import dataclass
from typing import Protocol


class PaymentGateway(Protocol):
    def charge(self, payment: Payment) -> ChargeOutcome: ...


@dataclass(frozen=True, slots=True)
class SubmitPayment:
    gateway: PaymentGateway
    payments: PaymentRepository

    def __call__(self, account: AccountId, amount: Money) -> SubmitResult:
        payment = initiate_payment(account, amount)   # domain decides
        outcome = self.gateway.charge(payment)        # port does I/O
        self.payments.save(complete_with(payment, outcome))

        if outcome.approved:
            return Accepted(id=payment.id)
        return Declined(reason=outcome.reason)
```

## Key rules

- Dependencies arrive through the constructor, never imported from an adapter
  module. Only the composition root knows the concrete implementation.
- The port is a `Protocol` declared in the application layer and satisfied
  structurally by the adapter, so the dependency points inward and the adapter
  imports nothing from the application to prove it.
- Return a tagged union of frozen dataclasses for expected outcomes. Reserve
  `raise` for the genuinely unexpected.
- Never return an ORM model or a transport dict - return domain types.
- The use case is the transaction boundary, not the handler and not the repository.
- Business rules live in domain functions. The use case sequences work; it does
  not encode the rules.
