---
name: using-standards
description: Read the team's coding standards before writing or changing code. Use when starting any coding task in a repo that has a dev-playbook standards project - to get its guardrails, definition of done, per-language rules and the workflow for the task at hand. Also covers how to name the project correctly on every dev-playbook tool call.
---

# Using the team's standards

The dev-playbook MCP server holds this team's coding standards: guardrails,
definition of done, per-language rules, and a workflow per kind of task. Read
them before you write code, not after.

## The `project` argument is the workspace directory name

Every dev-playbook tool takes a `project`. Getting it wrong returns another
team's standards, which is worse than returning nothing.

1. **`project` is the basename of the current workspace directory.** Working in
   `/home/you/code/NexRe` → `project="NexRe"`. Working in
   `/home/you/code/billing-api` → `project="billing-api"`. Matching is
   case-insensitive, so `nexre` and `NexRe` both resolve.
2. **Never substitute a different project.** If the directory name is not a
   known project, pass it anyway and read the error - it names what does exist.
   Do not pass `nexre` while editing `billing-api` because `nexre` happens to
   be in the store.
3. **Never omit it**, and never pick a convenient one to make a call succeed.

If the workspace has no standards project, that is the signal to run
`/dev-playbook:scaffold-standards`, not to borrow someone else's.

## The tools

These are the plugin's MCP tools - one read tool per kind of document. Their
scoped names are what you call (prefix `mcp__plugin_dev-playbook_dev-playbook__`):

| Call | For |
|---|---|
| `playbook_get_guardrails(project)` | The always-on MUST / MUST NOT rules and git conventions. Read first. |
| `playbook_get_workflow(project, intent? \| name?)` | The workflow matching what you are about to do (by `intent`), or one by `name`. |
| `playbook_get_standards(project, language)` | A language's standards, testing rules and anti-patterns. `language` is required. |
| `playbook_get_patterns(project, name?)` | Implementation patterns (repository, use-case, …); omit `name` to list them. |
| `playbook_get_agents(project)` | AGENTS.md, ARCHITECTURE.md and the glossary - identity, precedence, context. |
| `playbook_get_gates(project, language?)` | The definition of done and the verify scripts. Read before you call a change done. |
| `playbook_find_standards(project, query?, type?, top_k?)` | Search everything, or list it when given no query. |
| `playbook_list_templates(language?)` / `playbook_scaffold_standards(project, languages[], ...)` | The pack catalog and the **write** tool that creates a project. See `/dev-playbook:scaffold-standards`. |

## How to use them

1. **Read the guardrails first** - `playbook_get_guardrails(project)`. The
   SessionStart hook injects them too, but read them if the session is long.
2. **Get the workflow for the task** - `playbook_get_workflow(project,
   intent="...")`. `intent` is what you are actually about to do, in your own
   words ("fix the null pointer in OrderService", "add pagination to /orders"),
   so a vague intent gets you a vague workflow.
3. **Read the relevant rules and patterns** - `playbook_get_standards(project,
   language="...")` for the language you are touching, and
   `playbook_get_patterns(project)` for the shapes to follow. Every response
   ends with a Next Calls section written as literal calls - follow those rather
   than guessing.
4. **Search when a question is unanswered** - `playbook_find_standards(project,
   query="error handling")`. With no `query` it lists the whole project.
5. **Check your work against the definition of done** before you say you are
   finished: `playbook_get_gates(project)`.

## When there are no standards yet

The tools will tell you the project is unknown and name the projects that do
exist. Do not fall back to one of those. Offer to scaffold: run
`/dev-playbook:scaffold-standards`, or call `playbook_list_templates` and then
`playbook_scaffold_standards` with `dry_run=true`.
