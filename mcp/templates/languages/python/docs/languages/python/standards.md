---
title: Python standards - {{project}}
description: Coding standards for Python in {{project}}, based on PEP 8, ruff and mypy strict.
tags: [python, standards, style]
---

# Python standards - {{project}}

Baseline: **Python 3.12**, source under `src/{{python_package}}/`, packaging and
tool configuration in a single `pyproject.toml` (PEP 621). Lint and format are
[ruff](https://docs.astral.sh/ruff/); type checking is `mypy --strict`.

## Tooling

One tool per job, all configured in `pyproject.toml` - never a second config file
that can disagree with it:

| Job | Tool | Command |
| --- | --- | --- |
| Environment and locking | uv | `uv sync` |
| Format | ruff format | `ruff format .` |
| Lint | ruff check | `ruff check .` |
| Types | mypy | `mypy src tests` |
| Tests | pytest | `pytest` |
| Dependency audit | pip-audit | `uv run pip-audit` |

`ruff check --fix` runs **before** `ruff format`: a fix can emit code that then
needs reformatting, and the reverse order leaves the tree dirty.

## Project layout

The `src/` layout is not decoration. Without it, `import {{python_package}}`
resolves to the working directory, so the tests exercise the source tree rather
than the installed package and a missing entry in `pyproject.toml` goes unnoticed
until release.

```
pyproject.toml
src/{{python_package}}/__init__.py
tests/
```

## Style

| Thing | Convention | Example |
| --- | --- | --- |
| Module / package | lower_snake_case | `payment_gateway.py` |
| Class / exception | PascalCase | `PaymentRequest`, `PaymentDeclined` |
| Function / variable | lower_snake_case | `submit_payment` |
| Constant | UPPER_SNAKE_CASE | `MAX_RETRIES` |
| Private to a module | single leading underscore | `_normalise` |
| Type variable | Descriptive PascalCase | `ResponseT` |
| Boolean | `is_` / `has_` / `can_` prefix | `is_expired` |

Line length is 88 (the ruff default). Indentation is four spaces. Import order is
whatever `ruff check --select I --fix` produces, and never edited by hand.

## Typing

- Every function that is not a test declares parameter and return types. `--strict`
  makes an untyped `def` an error, which is the point.
- Prefer the builtin generics (`list[str]`, `dict[str, int]`, `X | None`) over the
  `typing` aliases; they are the language now.
- `Any` is a hole in the type system. Use `object` and narrow, or a `Protocol`, or
  a `TypedDict` for a known dict shape.
- Model closed sets of states with `enum.Enum` or a `Literal` union, and closed
  sets of shapes with a tagged union of frozen dataclasses.
- Accept the widest useful type (`Sequence`, `Iterable`, `Mapping`) and return the
  narrowest concrete one (`list`, `dict`).
- Use `@dataclass(frozen=True, slots=True)` for value objects; mutability is opt-in,
  not the default.

## Errors

- Raise a project-specific exception deriving from one package root exception, so
  callers can catch the package without catching `Exception`.
- `raise ... from err` whenever re-raising, so the original traceback survives.
- Catch the narrowest exception that can actually occur. A bare `except:` also
  catches `KeyboardInterrupt` and `SystemExit`.
- Never swallow an exception silently. Handle it, add context, or let it rise.

## Async

- Do not mix blocking I/O into `async def`. A blocking call stalls the whole event
  loop, not just its own coroutine.
- Every task created with `asyncio.create_task` is awaited or held in a
  `TaskGroup`; a bare `create_task` result can be garbage collected mid-flight.
- Use `asyncio.TaskGroup` for concurrent work and `asyncio.timeout` for deadlines.
- Cancellation is normal control flow. Never swallow `asyncio.CancelledError`.

## Modules

- `__init__.py` defines a package's public surface and nothing else. No I/O, no
  configuration reads, no client construction at import time.
- One module, one reason to change. A module that needs a `# --- section ---`
  comment to be navigable is two modules.
- No wildcard imports, in either direction.
