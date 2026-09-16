"""playbook_get_patterns - the project's implementation patterns.

Patterns are project-wide, addressed by name rather than by language: the
repository or use-case shape reads the same whichever language wrote it, so this
tool takes no `language`. With no `name` it lists them; with one it returns that
pattern in full.
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

NAME = "playbook_get_patterns"

_PREFIX = "patterns/"


def _pattern_rows(rows: list[FileRow]) -> list[FileRow]:
    return [r for r in rows if r.relative_path.startswith(_PREFIX)]


def _pattern_name(relative_path: str) -> str:
    return relative_path.rsplit("/", 1)[-1].removesuffix(".md")


DEFINITIONS: list[Tool] = [
    Tool(
        name=NAME,
        title="List or read implementation patterns",
        description=(
            "Lists the project's implementation patterns, or returns one by `name`. "
            "Patterns are project-wide, not per language - e.g. repository, "
            "use-case, viewmodel.\n\n"
            'Example: playbook_get_patterns(project="nexre", name="repository"). '
            "Omit `name` to list them all.\n\n"
            "For a language's rules use playbook_get_standards."
        ),
        annotations=READ_ONLY,
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_PARAM_DESC},
                "name": {
                    "type": "string",
                    "description": (
                        "A pattern name to read in full, e.g. 'repository'. Omit to list "
                        "every pattern the project has."
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
    wanted = as_str(arguments.get("name")).lower().removesuffix(".md")
    project = await resolve_project(store, requested)
    if project is None:
        ctx.status = "error"
        return error(await unknown_project(store, requested))

    patterns = _pattern_rows(await store.list_files(project))
    if not patterns:
        ctx.status = "error"
        return error(f"Project '{project}' has no patterns.")

    if wanted:
        matched = [r for r in patterns if _pattern_name(r.relative_path).lower() == wanted]
        if not matched:
            names = ", ".join(sorted(_pattern_name(r.relative_path) for r in patterns))
            ctx.status = "error"
            return error(
                f"Project '{project}' has no pattern named '{wanted}'. Available: {names}."
            )
        ctx.doc_path = matched[0].relative_path
        return text("\n\n".join(render_ref(row) for row in matched).rstrip() + "\n")

    lines = [f"# Patterns in '{project}'", "", f"{len(patterns)} patterns.", ""]
    for row in sorted(patterns, key=lambda r: r.relative_path):
        desc = f" - {row.description}" if row.description else ""
        lines.append(f"- **{_pattern_name(row.relative_path)}** {row.title}{desc}")
    lines.append(next_calls(project, [(patterns[0].relative_path, "Read one")]))
    return text("\n".join(lines).rstrip() + "\n")


__all__ = ["DEFINITIONS", "NAME", "dispatch"]
