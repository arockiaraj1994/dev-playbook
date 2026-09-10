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

These are the plugin's MCP tools. Their scoped names are what you call:

| Call | For |
|---|---|
| `mcp__plugin_dev-playbook_dev-playbook__playbook_start_task(project, intent)` | **Start here.** Returns the guardrails plus the workflow matching what you are about to do, and the refs to read next. |
| `mcp__plugin_dev-playbook_dev-playbook__playbook_get_standard(project, ref)` | One document. `ref` is a path (`core/guardrails.md`) or shorthand (`guardrails`, `workflow:bug-fix`). |
| `mcp__plugin_dev-playbook_dev-playbook__playbook_find_standards(project, query?, type?, top_k?)` | Search a project's standards, or list everything when given no query. |
| `mcp__plugin_dev-playbook_dev-playbook__playbook_list_templates(language?)` | The language pack catalog - only needed when scaffolding. |
| `mcp__plugin_dev-playbook_dev-playbook__playbook_scaffold_standards(project, languages[], ...)` | **Writes.** Creates a standards project. See `/dev-playbook:scaffold-standards`. |

## How to use them

1. **`playbook_start_task(project, intent)` first.** `intent` is what you are
   actually about to do, in your own words - "fix the null pointer in
   OrderService", "add pagination to /orders". It picks the workflow from that,
   so a vague intent gets you a vague workflow.
2. **Follow the refs it prints.** Every response ends with a Next Calls section
   written as literal tool calls. Read the ones relevant to your change through
   `playbook_get_standard` rather than guessing what a standard says.
3. **Search when you have a question the refs did not answer** -
   `playbook_find_standards(project, query="error handling")`. With no `query`
   it lists the whole project, which is the fastest way to see what exists.
4. **Check your work against the definition of done** before you say you are
   finished: `playbook_get_standard(project, ref="core/definition-of-done.md")`.

## When there are no standards yet

`playbook_start_task` will tell you the project is unknown and name the
projects that do exist. Do not fall back to one of those. Offer to scaffold:
run `/dev-playbook:scaffold-standards`, or call `playbook_list_templates` and
then `playbook_scaffold_standards` with `dry_run=true`.
