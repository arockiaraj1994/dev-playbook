---
title: Repository pattern - {{project}}
description: How persistence is isolated behind ports in {{project}}.
tags: [pattern, repository, persistence, kotlin]
---

# Pattern: Repository - {{project}}

**Use this when:** the application layer needs to load or store a domain object.

## Structure

```
{{package}}/application/port/PaymentRepository.kt   <- interface (inward)
{{package}}/adapter/outbound/DbPaymentRepository.kt <- implementation
{{package}}/adapter/outbound/PaymentRow.kt          <- storage shape
```

## Canonical example

```kotlin
// application/port - speaks domain, knows nothing about storage
interface PaymentRepository {
    fun observe(id: PaymentId): Flow<Payment?>
    suspend fun findById(id: PaymentId): Payment?
    suspend fun save(payment: Payment)
}

// adapter/outbound - owns the mapping in both directions
class DbPaymentRepository(
    private val dao: PaymentDao,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) : PaymentRepository {

    override fun observe(id: PaymentId): Flow<Payment?> =
        dao.observe(id.value).map { row -> row?.toDomain() }

    override suspend fun findById(id: PaymentId): Payment? =
        withContext(io) { dao.findById(id.value)?.toDomain() }

    override suspend fun save(payment: Payment) =
        withContext(io) { dao.upsert(payment.toRow()) }

    private fun PaymentRow.toDomain(): Payment = TODO("map fields")
    private fun Payment.toRow(): PaymentRow = TODO("map fields")
}
```

## Key rules

- The interface is named for the domain concept and lives in `application.port`.
  It must not mention SQL, rows, or any driver type.
- Mapping between the storage shape and the domain object happens **inside the
  adapter** - never in a use case, and never by making the domain aware of its table.
- Observable reads return `Flow`; one-shot reads and writes are `suspend`.
- Suspend functions are main-safe: switch to an injected dispatcher internally
  rather than requiring the caller to know.
- Return `null` for a single lookup that misses; return an empty list, never null,
  for a query.
- Every query is parameterized. No string interpolation into SQL, ever.
- One repository per aggregate, not one per table.
- Transaction control belongs to the use case; the repository participates in a
  transaction, it does not start one.
