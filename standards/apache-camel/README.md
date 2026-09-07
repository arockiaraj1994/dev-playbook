# Apache Camel - playbook

Project rules, patterns, and workflows for Apache Camel integration projects
(YAML DSL + Quarkus or Spring Boot + Java 21).

## For humans

- `AGENTS.md` - what an AI agent should be when working in this project.
- `INDEX.md` - auto-generated trigger map (do not edit by hand).
- `core/` - always-on guardrails, definition-of-done, glossary.
- `architecture/` - system overview and ADRs.
- `languages/java/` - Java + Camel coding standards, testing, anti-patterns.
- `patterns/` - canonical route patterns (SFTP, REST, messaging, error-handling).
- `skills/` - verb-noun playbooks (add-route, debug-route).
- `workflows/` - task-driven flows (new-feature, bug-fix, security-fix, refactor).
- `gates/` - executable verification scripts; run `bash gates/scripts/verify-java.sh` before claiming done.

## For AI agents

Call `playbook_start(project="apache-camel", intent="<what the user asked for>")`
first. The bundle returns guardrails + the matched workflow + the next
tool calls to make.
