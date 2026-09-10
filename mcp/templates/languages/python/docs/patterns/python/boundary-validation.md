---
title: Boundary validation pattern - {{project}}
description: How external data is parsed and narrowed at the edges of {{project}}.
tags: [pattern, validation, python, security]
---

# Pattern: Boundary validation - {{project}}

**Use this when:** data enters the process - HTTP request, queue message,
environment variable, file, or third-party response.

## The rule

Everything crossing a trust boundary is untyped until a runtime validator has
parsed it. `mypy` checks the code you wrote; it never saw the payload.

## Canonical example

```python
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class CreatePayment(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    account_id: UUID
    amount_minor: int = Field(gt=0, le=1_000_000)
    currency: Literal["GBP", "USD", "EUR"]


def handle_create_payment(body: object) -> Response:
    try:
        request = CreatePayment.model_validate(body)
    except ValidationError:
        logger.info("rejected malformed create_payment")
        return json_response({"error": "invalid_request"}, status=400)

    result = submit_payment(request.account_id, Money(request.amount_minor))
    return json_response(to_response_body(result), status=200)
```

## Key rules

- **Parse, don't cast.** `cast(CreatePayment, body)` is a lie mypy believes;
  `model_validate` is a check that runs. The same applies to a bare
  `TypedDict` annotation on a decoded JSON body.
- **Forbid unknown fields.** `extra="forbid"` turns a silently ignored typo -
  or a smuggled `is_admin` - into a 400.
- **Bound every value.** String lengths, numeric ranges, collection sizes. An
  unbounded input is a denial-of-service vector, and a 10 MB JSON body is an
  outage.
- Validate at the edge, once. Inner layers receive types already known to be
  valid and must not re-check.
- Return a **generic** error to the caller and log the detail. Echoing validation
  internals hands an attacker the shape of your model.
- Environment variables get the same treatment at startup, so the process fails
  immediately on misconfiguration rather than at the first request.
- The same discipline applies to a response you receive. A third-party API is
  outside your trust boundary too.
