"""playbook_get_gates - the definition of done and the verification gates.

Returns gates/definition-of-done.md (the completion checklist) plus the gate
scripts that check it: the per-language verify script and any release scripts.
`language` narrows the verify script to one language; without it every gate is
returned.
"""

from __future__ import annotations

from mcp.types import TextContent, Tool

from standards_store import FileRow, StandardsStore
from tools.common import (
    PROJECT_PARAM_DESC,
    READ_ONLY,
    as_str,
    error,
    render_ref,
    resolve_project,
    text,
    unknown_project,
)

NAME = "playbook_get_gates"

_DOD = "gates/definition-of-done.md"
_README = "gates/README.md"
_SCRIPTS = "gates/scripts/"


def _is_verify(relative_path: str) -> bool:
    name = relative_path.rsplit("/", 1)[-1]
    return name.startswith("verify-") and name.endswith(".sh")


DEFINITIONS: list[Tool] = [
    Tool(
        name=NAME,
        title="Read the definition of done and verification gates",
        description=(
            "Returns the definition of done plus the verification gates: the verify "
            "script for a language (gates/scripts/verify-<lang>.sh) and any release "
            "scripts, with how to run them. Call before treating a change as "
            "complete.\n\n"
            'Example: playbook_get_gates(project="nexre", language="kotlin").\n\n'
            "Reads the gate scripts; it does not run them."
        ),
        annotations=READ_ONLY,
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_PARAM_DESC},
                "language": {
                    "type": "string",
                    "description": (
                        "Optional. Narrow the verify script to one language, e.g. "
                        "'kotlin'. Omitted, every gate script is returned."
                    ),
                },
            },
            "required": ["project"],
        },
    )
]


def _selected(rows: list[FileRow], language: str) -> list[FileRow]:
    """Definition of done and README first, then the relevant gate scripts."""
    by_path = {r.relative_path: r for r in rows}
    ordered: list[FileRow] = []
    for path in (_DOD, _README):
        if path in by_path:
            ordered.append(by_path[path])

    scripts = sorted(
        (r for r in rows if r.relative_path.startswith(_SCRIPTS)),
        key=lambda r: r.relative_path,
    )
    for row in scripts:
        # With a language given, keep only that language's verify script; always
        # keep non-verify scripts (release/tooling) whichever language it is.
        if language and _is_verify(row.relative_path):
            if row.relative_path.rsplit("/", 1)[-1] != f"verify-{language}.sh":
                continue
        ordered.append(row)
    return ordered


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

    rows = _selected(await store.list_files(project), language)
    if not rows:
        ctx.status = "error"
        return error(
            f"Project '{project}' has no definition of done or gate scripts. Its "
            "standards are incomplete - say so before relying on them."
        )

    ctx.doc_path = rows[0].relative_path
    parts = [f"# Definition of done and gates for '{project}'", ""]
    parts += [render_ref(row) for row in rows]
    return text("\n\n".join(p for p in parts if p).rstrip() + "\n")


__all__ = ["DEFINITIONS", "NAME", "dispatch"]
