---
title: Python testing - {{project}}
description: How {{project}} is tested - scope, structure, and what a good test looks like.
tags: [python, testing]
---

# Python testing - {{project}}

Runner: **pytest**. Tests live in `tests/`, mirroring the package layout, named
`test_*.py`. The suite runs from a clean checkout with `uv run pytest` and needs
no manual setup.

## What to test

| Layer | Test it? | Why |
| --- | --- | --- |
| `domain/` | Always | Pure functions - cheapest and most valuable to cover |
| `application/` | Always | Use case orchestration and error mapping |
| `adapters/inbound/` | Yes | Validation, status codes, response shape |
| `adapters/outbound/` | Yes | Query correctness, against a real engine where possible |
| `config/` | Rarely | Wiring is verified by the tests above passing |

## Structure

Arrange, act, assert - with a blank line between the three. One behaviour per test.

```python
def test_rejects_an_expired_card() -> None:
    card = expired_card()

    result = submit_payment(card, Money("10.00"))

    assert result == Declined(reason="expired_card")
```

Parametrise over data, never over behaviour - the moment a case needs its own
`if`, it needs its own test:

```python
@pytest.mark.parametrize(
    ("amount", "expected"),
    [("0.00", "invalid_amount"), ("-1.00", "invalid_amount")],
)
def test_rejects_non_positive_amounts(amount: str, expected: str) -> None:
    assert submit_payment(valid_card(), Money(amount)).reason == expected
```

## Rules

- **Name the behaviour.** `test_rejects_an_expired_card`, not `test_submit_2`.
- **Prefer fakes to mocks.** A small in-memory implementation of a port reads
  better and breaks less than a stack of patched calls. Reserve mocks for
  verifying an interaction genuinely happened.
- **Never patch what you own.** `unittest.mock.patch` on your own module hides a
  missing seam; pass the dependency in instead. Patching a string path also
  survives a rename that should have failed.
- **No logic in tests.** A conditional in a test means it tests two things, or
  reimplements the code under test.
- **Deterministic.** Freeze the clock, seed randomness, never hit the real
  network, never `sleep` to wait for something.
- **Independent.** Tests pass in any order and under `pytest -p xdist`; no shared
  mutable module state, no reliance on a previous test's rows.
- **Fixtures build data, not behaviour.** A fixture that asserts is a test in
  disguise; keep the assertions in the test body where the failure is readable.
- **Assert on outcomes, not internals.** Asserting that a private helper ran
  freezes the implementation.
- **A bug fix starts with a failing test** that reproduces the bug.

## Coverage

Coverage is a smoke alarm, not a goal. A line reached by a test that asserts
nothing is not covered in any useful sense. Track branch coverage
(`--cov-branch`), look at what is missing, and add the test the gap describes -
never a test whose only purpose is to raise the number.
