---
title: Use case pattern - {{project}}
description: How application-layer use cases are structured in {{project}}.
tags: [pattern, use-case, kotlin]
---

# Pattern: Use case - {{project}}

**Use this when:** adding a business operation that orchestrates domain objects
and outbound ports.

## Structure

```
{{package}}/application/SubmitPayment.kt          <- the use case
{{package}}/application/port/PaymentGateway.kt    <- outbound port it needs
{{package}}/adapter/outbound/AcmeGateway.kt       <- implements the port
```

## Canonical example

```kotlin
class SubmitPayment(
    private val gateway: PaymentGateway,
    private val payments: PaymentRepository,
) {
    sealed interface Result {
        data class Accepted(val id: PaymentId) : Result
        data class Declined(val reason: DeclineReason) : Result
    }

    suspend operator fun invoke(account: AccountId, amount: Money): Result {
        val payment = Payment.initiate(account, amount)   // domain decides
        val outcome = gateway.charge(payment)             // port does I/O
        payments.save(payment.completedWith(outcome))

        return when (outcome) {
            is Charged  -> Result.Accepted(payment.id)
            is Refused  -> Result.Declined(outcome.reason)
        }
    }
}
```

## Key rules

- Collaborators are constructor parameters. No service locator, no global access.
- The port interface lives in `application.port` and is implemented in
  `adapter.outbound`, so the dependency points inward.
- A single entry point: prefer `operator fun invoke` for a one-operation use case.
- Return a domain type or a sealed `Result`. Never a stored row or a transport DTO.
- Map expected failures to result variants; let genuinely exceptional conditions
  propagate, and never swallow `CancellationException`.
- `suspend` for one-shot work, `Flow` for a stream. The use case is main-safe.
- The use case is the transaction boundary - not the handler, not the repository.
- Business rules stay in the domain object. The use case sequences work; it does
  not encode the rules.
