"""playbook_get_agents - the project's identity and context documents.

Returns AGENTS.md (working conventions and precedence), ARCHITECTURE.md (module
boundaries) and the glossary in one call. Bodies come from common.render_ref, so
they read identically to every other tool that returns the same document.
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

NAME = "playbook_get_agents"

# The identity/context set, in reading order. Missing ones are skipped.
_PATHS = ("AGENTS.md", "ARCHITECTURE.md", "glossary.md")

DEFINITIONS: list[Tool] = [
    Tool(
        name=NAME,
        title="Read the project's identity and context",
        description=(
            "Returns the project's AGENTS.md (working conventions, precedence, and "
            "the map of which document governs what), ARCHITECTURE.md (module "
            "boundaries and dependency rules), and the glossary of domain terms. "
            "Read this to orient.\n\n"
            'Example: playbook_get_agents(project="nexre").\n\n'
            "For the always-on rules use playbook_get_guardrails; for a language's "
            "rules use playbook_get_standards."
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
            f"Project '{project}' has no AGENTS.md, ARCHITECTURE.md or glossary. "
            "Its standards are incomplete - say so before relying on them."
        )

    ctx.doc_path = rows[0].relative_path
    parts = [f"# Identity and context for '{project}'", ""]
    parts += [render_ref(row) for row in rows]

    known = {r.relative_path for r in await store.list_files(project)}
    follow = [
        (path, label)
        for path, label in (("guardrails.md", "Always-on rules and git practice"),)
        if path in known
    ]
    parts.append(next_calls(project, follow))
    return text("\n\n".join(p for p in parts if p).rstrip() + "\n")


__all__ = ["DEFINITIONS", "NAME", "dispatch"]
