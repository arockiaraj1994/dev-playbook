---
title: Workflow - New feature
description: Adding a new integration route, transformation, or producer.
triggers: [new feature, add a route, add a new connector, build a new flow, new integration]
gates: [verify-java]
see_also: [skill:add-route, pattern:messaging-route, pattern:rest-producer, pattern:sftp-route, language:java/standards, language:java/testing]
---

# Workflow - New feature

Use this when the user wants a NEW integration flow added to the project.

## Steps

1. **Confirm scope.** Ask which external systems are involved, the message format (JSON/XML/CSV/binary), and the error contract (DLQ destination, retry budget). Don't guess.
2. **Locate the right home.** Read existing files under `src/main/resources/camel/<domain>/` and `src/main/java/.../processor/`. Match the prevailing folder convention; don't invent a new one.
3. **Pick the pattern.** Pull the canonical pattern doc that matches the inbound protocol (SFTP / REST / messaging) and load `patterns/error-handling.md` for the DLQ shape.
4. **Author the YAML route.** Set `routeId`, use property placeholders, and define `errorHandler` / `onException` + the DLQ route.
5. **Author the processor + service bean.** Processor translates `Exchange` ↔ domain types; service has no Camel dependency.
6. **Tests.** Cover happy path AND DLQ path (see `languages/java/testing.md`). No `Thread.sleep`.
7. **Configuration.** Add new keys to `application.properties` with env-var fallback. No literal hosts or credentials.
8. **Run the gate.** `bash gates/scripts/verify-java.sh`.
9. **Self-review.** Diff is minimal? Commit message explains WHY?

## Done

- All boxes in `core/definition-of-done.md` are checked.
- `playbook_start` for "fix a bug in this route" would naturally pick up the new file.
