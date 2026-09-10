---
title: Use case pattern - {{project}}
description: How application-layer use cases are structured in {{project}}.
tags: [pattern, use-case, rust]
---

# Pattern: Use case - {{project}}

**Use this when:** adding a business operation that orchestrates domain logic and
outbound dependencies.

## Structure

```
src/application/submit_payment.rs        <- the use case and the traits it needs
src/adapters/outbound/acme_gateway.rs    <- implements one of them
src/main.rs                              <- constructs both and joins them
```

## Canonical example

```rust
use async_trait::async_trait;

/// Declared here because this is where it is consumed.
#[async_trait]
pub trait PaymentGateway: Send + Sync {
    async fn charge(&self, payment: &Payment) -> Result<ChargeOutcome, GatewayError>;
}

pub struct SubmitPayment<G, R> {
    gateway: G,
    payments: R,
}

impl<G: PaymentGateway, R: PaymentRepository> SubmitPayment<G, R> {
    pub fn new(gateway: G, payments: R) -> Self {
        Self { gateway, payments }
    }

    pub async fn run(&self, account: AccountId, amount: Money) -> Result<Accepted, SubmitError> {
        let payment = Payment::initiate(account, amount);          // domain decides

        let outcome = self
            .gateway
            .charge(&payment)
            .await
            .map_err(SubmitError::Gateway)?;                       // trait does I/O

        self.payments
            .save(&payment.complete_with(&outcome))
            .await
            .map_err(SubmitError::Storage)?;

        outcome.accepted().ok_or(SubmitError::Declined(outcome.reason))
    }
}
```

## Key rules

- The trait is declared in the **application** layer, next to the code that calls
  it. The adapter implements it; the application never imports the adapter.
- Dependencies arrive through `new`. Only `main.rs` names a concrete type.
- Generic parameters keep the call static and testable; a `dyn Trait` behind an
  `Arc` is equally valid when the set of implementations is chosen at run time.
  Pick one per project and stay with it.
- The error type is an `enum` with a variant per failure mode, derived with
  `thiserror`, so the caller can match rather than parse a string.
- Return domain types. Never return a database row or a transport struct.
- The use case is the transaction boundary, not the handler and not the repository.
