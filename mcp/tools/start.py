"""playbook_start_task - the entry point an agent calls before doing work.

Matches the stated intent against the `triggers:` frontmatter every workflow
carries (TEMPLATE_SPEC.md), then returns the guardrails and the matched
workflow together with the calls that follow.

The document bodies come from get.render_ref - this module formats none of its
own. That is the point: issue #380 found the same guardrails text had been
duplicated between start and get twice over, because earlier passes merged tool
names without merging renderers. test_tools_start asserts byte-identity.
"""

from __future__ import annotations

import re

from mcp.types import TextContent, Tool

from standards_store import FileRow, StandardsStore
from tools import get
from tools.common import (
    PROJECT_PARAM_DESC,
    READ_ONLY,
    as_str,
    error,
    next_calls,
    resolve_project,
    text,
    unknown_project,
)
from tools.refs import format_ref

NAME = "playbook_start_task"

_WORD = re.compile(r"[a-z0-9]+")
_TRIGGERS = re.compile(r"^triggers:\s*\[(.*?)\]\s*$", re.MULTILINE | re.DOTALL)

DEFINITIONS: list[Tool] = [
    Tool(
        name=NAME,
        title="Start a task under a project's standards",
        description=(
            "Call this first, before writing or changing any code in a project "
            "that has standards. Give it what you are about to do and it returns "
            "that project's guardrails, the workflow matching your intent, and "
            "the documents to read next.\n\n"
            'Example: playbook_start_task(project="nexre", intent="fix the null '
            'pointer in the payment retry path") returns the guardrails plus the '
            "bug-fix workflow.\n\n"
            "Limitations: if the project has no standards yet this returns "
            "nothing useful - scaffold them with playbook_scaffold_standards "
            "instead. It reads standards; it does not read your code."
        ),
        annotations=READ_ONLY,
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_PARAM_DESC},
                "intent": {
                    "type": "string",
                    "description": (
                        "What you are about to do, in the user's own words where "
                        "possible - e.g. 'add pagination to the orders endpoint' or "
                        "'fix a crash on startup'. Used to pick the workflow."
                    ),
                },
            },
            "required": ["project", "intent"],
        },
    )
]


def _tokens(value: str) -> set[str]:
    return set(_WORD.findall(value.lower()))


def _triggers(row: FileRow) -> list[str]:
    """The trigger phrases a workflow declares in its frontmatter."""
    match = _TRIGGERS.search(row.frontmatter or "")
    if not match:
        return []
    return [t.strip().strip("\"'") for t in match.group(1).split(",") if t.strip()]


def match_workflow(rows: list[FileRow], intent: str) -> FileRow | None:
    """Pick the workflow whose triggers best fit the intent.

    A whole trigger phrase appearing in the intent is decisive; otherwise
    overlapping words decide, with the title as a weak tiebreak.
    """
    lowered = intent.lower()
    words = _tokens(intent)
    best: tuple[float, str, FileRow] | None = None

    for row in rows:
        if not row.relative_path.startswith("workflows/"):
            continue
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


async def dispatch(
    name: str, arguments: dict, ctx, store: StandardsStore
) -> list[TextContent] | None:
    if name != NAME:
        return None

    requested = as_str(arguments.get("project"))
    intent = as_str(arguments.get("intent"))
    project = await resolve_project(store, requested)
    if project is None:
        ctx.status = "error"
        return error(await unknown_project(store, requested))

    rows = await store.list_files(project)
    guardrails = await get.resolve(store, project, "guardrails")
    workflow = match_workflow(rows, intent)
    ctx.doc_path = workflow.relative_path if workflow else None

    parts = [
        f"# Starting work in '{project}'",
        "",
        f"Intent: {intent}",
        "",
    ]

    if guardrails is not None:
        parts += [get.render_ref(guardrails), ""]
    else:
        parts += [
            "This project has no `core/guardrails.md`. Its standards are "
            "incomplete - say so before relying on them.",
            "",
        ]

    if workflow is not None:
        parts += [get.render_ref(workflow), ""]
    else:
        available = ", ".join(
            format_ref(r.relative_path) for r in rows if r.relative_path.startswith("workflows/")
        )
        parts += [
            "## No matching workflow",
            "",
            f"Nothing matched that intent. Available: {available or '(none)'}. "
            "Pick the closest one with playbook_get_standard, or proceed on the "
            "guardrails alone and say that you did.",
            "",
        ]

    follow = [("definition-of-done", "Definition of done"), ("git", "Git practice")]
    known = {r.relative_path for r in rows}
    follow = [(ref, label) for ref, label in follow if f"core/{ref}.md" in known]
    parts.append(next_calls(project, follow))

    return text("\n".join(parts).rstrip() + "\n")


__all__ = ["DEFINITIONS", "NAME", "dispatch", "match_workflow"]
