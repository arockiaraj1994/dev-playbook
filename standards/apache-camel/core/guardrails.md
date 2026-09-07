---
title: Guardrails - Apache Camel
description: Always-on MUST / MUST NOT rules. Loaded via playbook_get(ref="guardrails") on every task.
tags: [guardrails, security, scope]
---

# Guardrails - Apache Camel

These rules are non-negotiable. Re-read them at the start of every task and
again before claiming a task complete. They are intentionally short - see
`languages/java/anti-patterns.md` and `core/definition-of-done.md` for the
full detail.

## MUST

- **Scope.** Change ONLY what is asked. One task = one change. No incidental refactors, renames, or "while I'm here" cleanups.
- **Read before write.** Inspect existing routes, properties, and processors before adding anything new. Match prevailing names and patterns.
- **Ask, don't guess.** If requirements are ambiguous (auth strategy, message format, error contract), ask before coding.
- **Honesty.** If a proposed approach has a flaw, say so directly with a better alternative. If you don't know, say "I don't know."
- **YAML DSL only** for routes, unless the user explicitly asks for Java DSL.
- **Set `routeId` on every route.** Without it, logs and metrics are unidentifiable.
- **Define `errorHandler` / `onException` / DLQ in every consumer route.** No reliance on a global default.
- **DLQ routes must be implemented**, not just referenced. A `direct:dlq-*` endpoint with no consumer is a silent message drop.
- **Property placeholders for all config.** `{{sftp.host}}`, `{{env:VAR}}` - never literal hosts, ports, paths, or credentials.
- **TLS for all non-local HTTP.** No `http://` for production targets.

## MUST NOT

- **No hardcoded secrets** in YAML, properties, or Java. Use env vars or Kubernetes Secrets.
- **No unlimited retries.** `maximumRedeliveries: -1` causes runaway storms.
- **No silent error swallowing.** Empty `doCatch`, fake-success returns, or `catch (Throwable)` are forbidden.
- **No PII in logs.** Don't log message bodies that may contain credentials, card data, or personal info.
- **No business logic in `Processor` classes** - delegate to a service bean.
- **No Camel imports in service-layer beans.** Service code must be testable without a `CamelContext`.
- **No disabled SSL verification.**

## Definition of Done check

Before claiming a task complete, run the gate: `bash gates/scripts/verify-java.sh`.
See `core/definition-of-done.md` for the full checklist.
