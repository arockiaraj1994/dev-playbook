"""playbook_get_workflow - the task workflow for a piece of work.

With `intent`, matches the stated work against the `triggers:` frontmatter every
workflow carries and returns the best fit. With `name`, returns that workflow.
With neither, lists them. The matcher (match_workflow) lived in the old
start_task tool; it moved here when that entry point was retired.
"""

from __future__ import annotations

import re

from mcp.types import TextContent, Tool

from standards_store import FileRow, StandardsStore
from tools.common import (
    PROJECT_PARAM_DESC,
    READ_ONLY,
    as_str,
    error,
    next_calls,
    render_ref,
    resolve_project,
    text,
    unknown_project,
)

NAME = "playbook_get_workflow"

_PREFIX = "workflows/"
_WORD = re.compile(r"[a-z0-9]+")
_TRIGGERS = re.compile(r"^triggers:\s*\[(.*?)\]\s*$", re.MULTILINE | re.DOTALL)


def _tokens(value: str) -> set[str]:
    return set(_WORD.findall(value.lower()))


def _triggers(row: FileRow) -> list[str]:
    """The trigger phrases a workflow declares in its frontmatter."""
    match = _TRIGGERS.search(row.frontmatter or "")
    if not match:
        return []
    return [t.strip().strip("\"'") for t in match.group(1).split(",") if t.strip()]


def _workflow_rows(rows: list[FileRow]) -> list[FileRow]:
    return [r for r in rows if r.relative_path.startswith(_PREFIX)]


def _workflow_name(relative_path: str) -> str:
    return relative_path.rsplit("/", 1)[-1].removesuffix(".md")


def match_workflow(rows: list[FileRow], intent: str) -> FileRow | None:
    """Pick the workflow whose triggers best fit the intent.

    A whole trigger phrase appearing in the intent is decisive; otherwise
    overlapping words decide, with the title as a weak tiebreak.
    """
    lowered = intent.lower()
    words = _tokens(intent)
    best: tuple[float, str, FileRow] | None = None

    for row in _workflow_rows(rows):
        score = 0.0
        for trigger in _triggers(row):
            if trigger and trigger.lower() in lowered:
                score += 10.0 + len(trigger)
            else:
                score += 2.0 * len(words & _tokens(trigger))
        score += 1.0 * len(words & _tokens(row.title))
        if score > 0 and (best is None or score > best[0]):
            best = (score, row.relative_path, row)

    return best[2] if best else None


DEFINITIONS: list[Tool] = [
    Tool(
        name=NAME,
        title="Get the workflow for a task",
        description=(
            "Returns the task workflow whose triggers match `intent` (bug-fix, "
            "new-feature, security-fix, refactor and more), or a specific workflow "
            "by `name`; omit both to list them. Each workflow gives the ordered "
            "steps for that kind of task.\n\n"
            'Example: playbook_get_workflow(project="nexre", intent="fix a crash on '
            'startup") returns the bug-fix workflow.\n\n'
            "Read the always-on rules first with playbook_get_guardrails."
        ),
        annotations=READ_ONLY,
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_PARAM_DESC},
                "intent": {
                    "type": "string",
                    "description": (
                        "What you are about to do, in the user's own words - e.g. 'add "
                        "pagination to the orders endpoint' or 'fix a crash on startup'. "
                        "Used to pick the workflow."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "A specific workflow to read, e.g. 'bug-fix'. Omit to match on "
                        "`intent`, or omit both to list every workflow."
                    ),
                },
            },
            "required": ["project"],
        },
    )
]


async def dispatch(
    name: str, arguments: dict, ctx, store: StandardsStore
) -> list[TextContent] | None:
    if name != NAME:
        return None

    requested = as_str(arguments.get("project"))
    intent = as_str(arguments.get("intent"))
    wanted = as_str(arguments.get("name")).lower().removesuffix(".md")
    project = await resolve_project(store, requested)
    if project is None:
        ctx.status = "error"
        return error(await unknown_project(store, requested))

    workflows = _workflow_rows(await store.list_files(project))
    if not workflows:
        ctx.status = "error"
        return error(f"Project '{project}' has no workflows.")

    if wanted:
        matched = next(
            (r for r in workflows if _workflow_name(r.relative_path).lower() == wanted), None
        )
        if matched is None:
            names = ", ".join(sorted(_workflow_name(r.relative_path) for r in workflows))
            ctx.status = "error"
            return error(
                f"Project '{project}' has no workflow named '{wanted}'. Available: {names}."
            )
        ctx.doc_path = matched.relative_path
        return text(render_ref(matched).rstrip() + "\n")

    if intent:
        ctx.query = intent
        matched = match_workflow(workflows, intent)
        if matched is None:
            names = ", ".join(sorted(_workflow_name(r.relative_path) for r in workflows))
            ctx.status = "error"
            return error(
                f"Nothing matched intent '{intent}' in '{project}'. Available workflows: "
                f"{names}. Pick the closest with playbook_get_workflow(name=...)."
            )
        ctx.doc_path = matched.relative_path
        parts = [f"# Workflow for '{intent}' in '{project}'", "", render_ref(matched)]
        return text("\n\n".join(parts).rstrip() + "\n")

    lines = [f"# Workflows in '{project}'", "", f"{len(workflows)} workflows.", ""]
    for row in sorted(workflows, key=lambda r: r.relative_path):
        triggers = ", ".join(_triggers(row))
        hint = f" - triggers: {triggers}" if triggers else ""
        lines.append(f"- **{_workflow_name(row.relative_path)}** {row.title}{hint}")
    lines.append(next_calls(project, [(workflows[0].relative_path, "Read one")]))
    return text("\n".join(lines).rstrip() + "\n")


__all__ = ["DEFINITIONS", "NAME", "dispatch", "match_workflow"]
