---
title: Boundary validation pattern - {{project}}
description: How external data is parsed and narrowed at the edges of {{project}}.
tags: [pattern, validation, typescript, security]
---

# Pattern: Boundary validation - {{project}}

**Use this when:** data enters the process - HTTP request, queue message,
environment variable, file, or third-party response.

## The rule

Everything crossing a trust boundary is `unknown` until a runtime schema has
parsed it. The compiler cannot check data it never saw.

## Canonical example

```ts
import { z } from "zod";

const CreatePayment = z.object({
  accountId: z.string().uuid(),
  amountMinor: z.number().int().positive().max(1_000_000),
  currency: z.enum(["GBP", "USD", "EUR"]),
});

export type CreatePayment = z.infer<typeof CreatePayment>;

export async function handleCreatePayment(req: Request): Promise<Response> {
  const parsed = CreatePayment.safeParse(await req.json());
  if (!parsed.success) {
    return json({ error: "invalid_request" }, 400);   // generic, no internals
  }

  const result = await submitPayment(parsed.data);
  return json(toResponseBody(result), 200);
}
```

## Key rules

- **Parse, don't assert.** `as CreatePayment` is a lie the compiler believes;
  `safeParse` is a check that runs.
- Derive the static type **from** the schema, so the two can never drift apart.
- Bound every value: string lengths, numeric ranges, array sizes. An unbounded
  input is a denial-of-service vector.
- Validate at the edge, once. Inner layers receive types that are already known
  to be valid and must not re-check.
- Return a **generic** error to the caller and log the detail. Echoing the
  validation internals tells an attacker the shape of your model.
- Environment variables get the same treatment at startup, so the process fails
  immediately on misconfiguration rather than at the first request.
