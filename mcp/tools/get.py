"""playbook_get_standard - fetch one standards document by ref.

`render_ref` is the single renderer for a document body. playbook_start_task
composes it rather than formatting documents of its own, and a test asserts the
two produce identical bytes. Issue #380 found that duplication had crept back
twice, because earlier passes merged tool *names* without merging their
*renderers*; composing the function is what actually prevents it.
"""

from __future__ import annotations

from mcp.types import TextContent, Tool

from standards_store import FileRow, StandardsStore
from tools.common import (
    PROJECT_PARAM_DESC,
    READ_ONLY,
    as_str,
    error,
    resolve_project,
    text,
    unknown_project,
)
from tools.refs import candidate_paths, format_ref

NAME = "playbook_get_standard"

DEFINITIONS: list[Tool] = [
    Tool(
        name=NAME,
        title="Read a standards document",
        description=(
            "Fetch one document from a project's standards by reference: the "
            "guardrails, the definition of done, a language rule sheet, a "
            "workflow, or a gate script. Follow a `ref` printed in a Next Calls "
            "block verbatim - it is already in the right form.\n\n"
            'Example: playbook_get_standard(project="nexre", ref="guardrails") '
            'before writing code, or ref="workflow:bug-fix" when fixing a bug.\n\n'
            "Limitations: reads one document. To search across them use "
            "playbook_find_standards; to see where to begin a task use "
            "playbook_start_task."
        ),
        annotations=READ_ONLY,
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_PARAM_DESC},
                "ref": {
                    "type": "string",
                    "description": (
                        "The document to read. A path such as 'core/guardrails.md', "
                        "a shorthand such as 'guardrails', 'agents', "
                        "'definition-of-done', or a prefixed form such as "
                        "'workflow:bug-fix' or 'gate:scripts/verify-java.sh'."
                    ),
                },
            },
            "required": ["project", "ref"],
        },
    )
]


async def resolve(store: StandardsStore, project: str, ref: str) -> FileRow | None:
    """Find the row a ref names, trying each candidate path in order."""
    for path in candidate_paths(ref):
        row = await store.get_file(project, path)
        if row is not None:
            return row
    return None


def render_ref(row: FileRow) -> str:
    """The canonical body for one document. The only place this is formatted."""
    header = f"# {row.title}"
    if row.description:
        header += f"\n\n{row.description}"
    location = f"`{row.relative_path}` in project `{row.project}`"
    if row.kind == "script":
        return "\n".join([header, "", location, "", "```sh", row.body.rstrip(), "```"])
    return "\n".join([header, "", location, "", "---", "", row.body.strip()])


async def miss(store: StandardsStore, project: str, ref: str) -> str:
    """What to say when a ref does not resolve: the paths that do exist."""
    rows = await store.list_files(project)
    paths = "\n".join(f"- `{format_ref(r.relative_path)}` ({r.relative_path})" for r in rows)
    return (
        f"No document matching ref '{ref}' in project '{project}'.\n\n"
        f"This project contains:\n{paths}\n\n"
        "Retry with one of those refs, or search with "
        f'playbook_find_standards(project="{project}", query="...").'
    )


async def dispatch(
    name: str, arguments: dict, ctx, store: StandardsStore
) -> list[TextContent] | None:
    if name != NAME:
        return None

    requested = as_str(arguments.get("project"))
    ref = as_str(arguments.get("ref"))
    project = await resolve_project(store, requested)
    if project is None:
        ctx.status = "error"
        return error(await unknown_project(store, requested))

    row = await resolve(store, project, ref)
    if row is None:
        ctx.status = "error"
        return error(await miss(store, project, ref))

    ctx.doc_path = row.relative_path
    return text(render_ref(row))


__all__ = ["DEFINITIONS", "NAME", "dispatch", "miss", "render_ref", "resolve"]
