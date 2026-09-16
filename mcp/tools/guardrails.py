"""playbook_get_guardrails - the project's always-on rules and git practice.

Returns guardrails.md (the MUST / MUST NOT rules that apply to every change) and
git.md (branching, commits, review, release) together - the cross-cutting rules
a change is held to regardless of language.
"""

from __future__ import annotations

from mcp.types import TextContent, Tool

from standards_store import StandardsStore
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

NAME = "playbook_get_guardrails"

_PATHS = ("guardrails.md", "git.md")

DEFINITIONS: list[Tool] = [
    Tool(
        name=NAME,
        title="Read the always-on rules and git conventions",
        description=(
            "Returns the project's always-on guardrails (the MUST / MUST NOT rules "
            "for every change) and its git conventions (branching, commit format, "
            "review, release). Read before writing code.\n\n"
            'Example: playbook_get_guardrails(project="nexre").\n\n'
            "For a language's rules use playbook_get_standards; for the completion "
            "checks use playbook_get_gates."
        ),
        annotations=READ_ONLY,
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_PARAM_DESC},
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
    project = await resolve_project(store, requested)
    if project is None:
        ctx.status = "error"
        return error(await unknown_project(store, requested))

    rows = [row for path in _PATHS if (row := await store.get_file(project, path)) is not None]
    if not rows:
        ctx.status = "error"
        return error(
            f"Project '{project}' has no guardrails.md or git.md. Its standards are "
            "incomplete - say so before relying on them."
        )

    ctx.doc_path = rows[0].relative_path
    parts = [f"# Guardrails for '{project}'", ""]
    parts += [render_ref(row) for row in rows]

    known = {r.relative_path for r in await store.list_files(project)}
    follow = [
        (path, label)
        for path, label in (("gates/definition-of-done.md", "Definition of done"),)
        if path in known
    ]
    parts.append(next_calls(project, follow))
    return text("\n\n".join(p for p in parts if p).rstrip() + "\n")


__all__ = ["DEFINITIONS", "NAME", "dispatch"]
