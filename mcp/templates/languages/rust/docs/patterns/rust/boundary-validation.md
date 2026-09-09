---
title: Boundary validation pattern - {{project}}
description: How external data is deserialised and checked at the edges of {{project}}.
tags: [pattern, validation, rust, security]
---

# Pattern: Boundary validation - {{project}}

**Use this when:** data enters the process - HTTP request, queue message,
environment variable, file, or third-party response.

## The rule

Deserialising is not validating. `serde` will happily build a `struct` whose
fields are individually well-typed and collectively nonsense. Parse into a
private wire type, then convert into a domain type that cannot be constructed
wrong.

## Canonical example

```rust
/// The wire shape. Private, and never used past this module.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct CreatePaymentBody {
    account_id: Uuid,
    amount_minor: i64,
    currency: String,
}

impl TryFrom<CreatePaymentBody> for CreatePayment {
    type Error = Invalid;

    fn try_from(b: CreatePaymentBody) -> Result<Self, Self::Error> {
        Ok(Self {
            account: AccountId(b.account_id),
            amount: Money::from_minor(b.amount_minor).ok_or(Invalid::Amount)?,
            currency: b.currency.parse().map_err(|_| Invalid::Currency)?,
        })
    }
}

pub async fn create_payment(
    State(app): State<App>,
    Json(body): Json<CreatePaymentBody>,   // body size capped by the router layer
) -> Result<Json<PaymentResponse>, ApiError> {
    let cmd = CreatePayment::try_from(body).map_err(|_| ApiError::InvalidRequest)?;

    let accepted = app.submit.run(cmd.account, cmd.amount).await.map_err(|err| {
        tracing::error!(?err, "submit payment failed");   // detail to the log
        ApiError::Internal                                 // generic to the caller
    })?;

    Ok(Json(accepted.into()))
}
```

## Key rules

- **`deny_unknown_fields`** turns a silently ignored typo - or a smuggled field -
  into a rejection.
- **`Option<T>` where absent and default differ.** Without it, a missing
  `amount_minor` with `#[serde(default)]` arrives as a well-typed `0`.
- **Bound the body** at the router or the reader. An unbounded read is a
  denial-of-service vector, and `serde_json` will allocate whatever it is given.
- **Convert into newtypes** whose constructors enforce the invariant, so inner
  layers cannot receive an unchecked value and must not re-check.
- Return a **generic** error to the caller and log the detail. A `Debug`-formatted
  error in a response discloses internals.
- Environment and configuration are parsed once at startup into a validated
  struct, so the process fails immediately on misconfiguration.
- A third-party response is outside the trust boundary too. Deserialise it with
  the same care.
