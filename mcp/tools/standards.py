"""playbook_get_standards - one language's coding standards.

`language` is required: standards, testing and anti-patterns documents only
exist per language (languages/<lang>/*). Listing every language's rules in one
call would bury the one the caller is working in, so the split is deliberate.
"""

from __future__ import annotations

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

NAME = "playbook_get_standards"


def _languages(rows: list[FileRow]) -> list[str]:
    """The language ids a project has standards for, from languages/<id>/*."""
    langs: list[str] = []
    for row in rows:
        parts = row.relative_path.split("/")
        if len(parts) > 2 and parts[0] == "languages" and parts[1] not in langs:
            langs.append(parts[1])
    return langs


DEFINITIONS: list[Tool] = [
    Tool(
        name=NAME,
        title="Read a language's coding standards",
        description=(
            "Returns one language's coding standards, testing rules and "
            "anti-patterns. `language` is required - these documents exist only per "
            "language (e.g. java, kotlin, python, typescript, go, rust).\n\n"
            'Example: playbook_get_standards(project="nexre", language="kotlin").\n\n'
            "For cross-cutting rules use playbook_get_guardrails; for reusable "
            "patterns use playbook_get_patterns."
        ),
        annotations=READ_ONLY,
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_PARAM_DESC},
                "language": {
                    "type": "string",
                    "description": (
                        "The language whose standards to read, e.g. 'kotlin'. Required - "
                        "call playbook_find_standards with no query to see which "
                        "languages a project covers."
                    ),
                },
            },
            "required": ["project", "language"],
        },
    )
]


async def dispatch(
    name: str, arguments: dict, ctx, store: StandardsStore
) -> list[TextContent] | None:
    if name != NAME:
        return None

    requested = as_str(arguments.get("project"))
    language = as_str(arguments.get("language")).lower()
    project = await resolve_project(store, requested)
    if project is None:
        ctx.status = "error"
        return error(await unknown_project(store, requested))

    rows = await store.list_files(project)
    available = _languages(rows)

    if not language:
        ctx.status = "error"
        return error(
            "playbook_get_standards needs a `language`. This project has standards "
            f"for: {', '.join(available) or '(none)'}."
        )

    prefix = f"languages/{language}/"
    matched = [r for r in rows if r.relative_path.startswith(prefix)]
    if not matched:
        ctx.status = "error"
        return error(
            f"Project '{project}' has no standards for language '{language}'. "
            f"Available: {', '.join(available) or '(none)'}."
        )

    ctx.doc_path = matched[0].relative_path
    parts = [f"# {language} standards in '{project}'", ""]
    parts += [render_ref(row) for row in matched]

    known = {r.relative_path for r in rows}
    follow = [
        (path, label)
        for path, label in (
            ("guardrails.md", "Always-on rules"),
            ("gates/definition-of-done.md", "Definition of done"),
        )
        if path in known
    ]
    parts.append(next_calls(project, follow))
    return text("\n\n".join(p for p in parts if p).rstrip() + "\n")


__all__ = ["DEFINITIONS", "NAME", "dispatch"]
