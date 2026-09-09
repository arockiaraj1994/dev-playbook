---
title: Repository pattern - {{project}}
description: How persistence is isolated behind ports in {{project}}.
tags: [pattern, repository, persistence, java]
---

# Pattern: Repository - {{project}}

**Use this when:** the application layer needs to load or store a domain object.

## Structure

```
{{package}}/application/port/PaymentRepository.java   <- interface (inward)
{{package}}/adapter/out/JdbcPaymentRepository.java    <- implementation
{{package}}/adapter/out/PaymentRecord.java            <- storage shape
```

## Canonical example

```java
// application/port - speaks domain, knows nothing about storage
public interface PaymentRepository {
    Optional<Payment> findById(PaymentId id);
    void save(Payment payment);
}

// adapter/out - owns the mapping in both directions
public final class JdbcPaymentRepository implements PaymentRepository {

    private final JdbcTemplate jdbc;

    @Override
    public Optional<Payment> findById(PaymentId id) {
        return jdbc.query("SELECT * FROM payments WHERE id = ?", ROW_MAPPER, id.value())
                   .stream().findFirst().map(this::toDomain);
    }

    private Payment toDomain(PaymentRecord r) { /* map fields */ }
    private PaymentRecord toRecord(Payment p) { /* map fields */ }
}
```

## Key rules

- The interface is named for the domain concept and lives in `application.port`.
  It must not mention SQL, rows, or any driver type.
- Mapping between the storage shape and the domain object happens **inside the
  adapter** - never in the use case, and never by making the domain object aware
  of its table.
- Return `Optional` for a single lookup that may miss; return an empty collection,
  never null, for a query.
- Every query is parameterized. No string concatenation, ever.
- Use one repository per aggregate, not one per table.
- Keep transaction control in the use case; the repository participates in a
  transaction, it does not start one.
