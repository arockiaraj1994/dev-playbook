---
title: Use case pattern - {{project}}
description: How application-layer use cases are structured in {{project}}.
tags: [pattern, use-case, java]
---

# Pattern: Use case - {{project}}

**Use this when:** adding a business operation that orchestrates domain objects
and outbound ports.

## Structure

```
{{package}}/application/SubmitPayment.java          <- the use case
{{package}}/application/port/PaymentGateway.java    <- outbound port it needs
{{package}}/adapter/out/AcmePaymentGateway.java     <- implements the port
```

## Canonical example

```java
public final class SubmitPayment {

    private final PaymentGateway gateway;
    private final PaymentRepository payments;

    public SubmitPayment(PaymentGateway gateway, PaymentRepository payments) {
        this.gateway = gateway;
        this.payments = payments;
    }

    public Result submit(AccountId account, Money amount) {
        var payment = Payment.initiate(account, amount);   // domain decides
        var outcome = gateway.charge(payment);             // port does I/O
        payments.save(payment.completedWith(outcome));
        return Result.of(outcome);
    }

    public sealed interface Result {
        record Accepted(PaymentId id) implements Result {}
        record Declined(String reason) implements Result {}
    }
}
```

## Key rules

- Collaborators arrive as `final` constructor parameters. No field injection.
- The port interface is declared in `application.port` and implemented in
  `adapter.out`, so the dependency points inward.
- Return a domain type or a sealed result. Never a persistence entity or a
  transport DTO.
- Map expected failures to result variants; let genuinely exceptional conditions
  propagate.
- The use case is the transaction boundary - not the controller, not the repository.
- Business decisions stay in the domain object. The use case sequences work; it
  does not encode the rules.
