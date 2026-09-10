"""playbook_find_standards - list or search a project's standards.

Scores in Python over the rows the store already returns. The BM25 engine
(mcp/search.py) went with the v1.0.0 cut, and a scaffolded project is roughly
25 documents, so an index would be machinery without a load to justify it.
SQLite FTS5 is the escalation if corpora grow by an order of magnitude.
"""

from __future__ import annotations

import re

from mcp.types import TextContent, Tool

from standards_store import FileRow, StandardsStore
from tools.common import (
    PROJECT_PARAM_DESC,
    READ_ONLY,
    as_int,
    as_str,
    error,
    resolve_project,
    text,
    unknown_project,
)
from tools.refs import format_ref

NAME = "playbook_find_standards"

# Path prefixes behind the `type` filter, so a caller can narrow without
# knowing the corpus layout.
_TYPES: dict[str, tuple[str, ...]] = {
    "workflow": ("workflows/",),
    "language": ("languages/",),
    "pattern": ("patterns/",),
    "gate": ("gates/",),
    "core": ("core/",),
}

_WORD = re.compile(r"[a-z0-9]+")

# Title and description are curated; a body match is weaker evidence.
_TITLE_WEIGHT = 8.0
_DESC_WEIGHT = 4.0
_FRONTMATTER_WEIGHT = 3.0
_BODY_WEIGHT = 1.0
_PHRASE_BONUS = 6.0

DEFINITIONS: list[Tool] = [
    Tool(
        name=NAME,
        title="Search a project's standards",
        description=(
            "Search a project's standards documents, or list them all when you "
            "pass no query. Use it to find the rule covering something specific "
            "before you write code, or to see what a project's standards contain.\n\n"
            'Example: playbook_find_standards(project="nexre", query="error '
            'handling in repositories") returns the ranked documents, each with '
            "the ref to read it in full.\n\n"
            "Limitations: returns snippets, not whole documents - read one with "
            "playbook_get_standard using the ref given. It searches one project's "
            "standards, not the codebase."
        ),
        annotations=READ_ONLY,
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": PROJECT_PARAM_DESC},
                "query": {
                    "type": "string",
                    "description": (
                        "Words to search for. Omit to list every document in the "
                        "project, which is the fastest way to see what it covers."
                    ),
                },
                "type": {
                    "type": "string",
                    "enum": sorted(_TYPES),
                    "description": (
                        "Optional. Restrict to one kind of document: workflow, "
                        "language, pattern, gate or core."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                    "description": "How many results to return. Default 10.",
                },
            },
            "required": ["project"],
        },
    )
]


def _tokens(value: str) -> list[str]:
    return _WORD.findall(value.lower())


def _score(row: FileRow, terms: list[str], phrase: str) -> float:
    fields = (
        (_tokens(row.title), _TITLE_WEIGHT),
        (_tokens(row.description), _DESC_WEIGHT),
        (_tokens(row.frontmatter), _FRONTMATTER_WEIGHT),
        (_tokens(row.body), _BODY_WEIGHT),
    )
    total = 0.0
    for term in terms:
        for tokens, weight in fields:
            hits = sum(1 for t in tokens if t == term or t.startswith(term))
            if hits:
                # Diminishing returns: ten mentions of a word do not make a
                # document ten times more relevant than one mention.
                total += weight * (1 + (hits - 1) ** 0.5)
    if phrase and phrase in row.body.lower():
        total += _PHRASE_BONUS
    return total


def _snippet(row: FileRow, terms: list[str], width: int = 220) -> str:
    body = " ".join(row.body.split())
    if not body:
        return ""
    lowered = body.lower()
    at = next((lowered.find(t) for t in terms if lowered.find(t) >= 0), -1)
    if at < 0:
        return body[:width] + ("..." if len(body) > width else "")
    start = max(0, at - width // 3)
    end = min(len(body), start + width)
    return ("..." if start else "") + body[start:end] + ("..." if end < len(body) else "")


def _filter(rows: list[FileRow], doc_type: str) -> list[FileRow]:
    prefixes = _TYPES.get(doc_type)
    if not prefixes:
        return rows
    return [r for r in rows if r.relative_path.startswith(prefixes)]


def _render_list(project: str, rows: list[FileRow], truncated: int) -> str:
    lines = [f"# Standards in '{project}'", "", f"{len(rows)} documents.", ""]
    for row in rows:
        ref = format_ref(row.relative_path)
        desc = f" - {row.description}" if row.description else ""
        lines.append(f"- `{ref}` **{row.title}**{desc}")
    if truncated:
        lines += [
            "",
            f"{truncated} more not shown. Raise top_k, or narrow with `type` or a query.",
        ]
    lines += [
        "",
        "## Next Calls",
        "",
        f'- Read one: `playbook_get_standard(project="{project}", ref="<ref above>")`',
    ]
    return "\n".join(lines)


def _render_results(
    project: str, query: str, scored: list[tuple[float, FileRow]], terms: list[str]
) -> str:
    lines = [f"# Standards matching '{query}' in '{project}'", ""]
    for score, row in scored:
        ref = format_ref(row.relative_path)
        lines += [
            f"## {row.title}",
            "",
            f"`{ref}` - score {score:.1f}",
            "",
            _snippet(row, terms),
            "",
        ]
    lines += [
        "## Next Calls",
        "",
        f'- Read one in full: `playbook_get_standard(project="{project}", ref="<ref above>")`',
    ]
    return "\n".join(lines)


def _no_match(project: str, query: str, doc_type: str) -> str:
    narrowed = f" of type '{doc_type}'" if doc_type else ""
    return (
        f"Nothing in '{project}'{narrowed} matches '{query}'.\n\n"
        "Try fewer or more general words, drop the `type` filter, or call "
        f'playbook_find_standards(project="{project}") with no query to see '
        "everything the project covers."
    )


async def dispatch(
    name: str, arguments: dict, ctx, store: StandardsStore
) -> list[TextContent] | None:
    if name != NAME:
        return None

    requested = as_str(arguments.get("project"))
    query = as_str(arguments.get("query"))
    doc_type = as_str(arguments.get("type")).lower()
    top_k = as_int(arguments.get("top_k"), 10, low=1, high=50)
    ctx.query = query or None

    project = await resolve_project(store, requested)
    if project is None:
        ctx.status = "error"
        return error(await unknown_project(store, requested))

    rows = _filter(await store.list_files(project), doc_type)

    if not query:
        shown = rows[:top_k]
        return text(_render_list(project, shown, len(rows) - len(shown)))

    terms = _tokens(query)
    phrase = " ".join(terms)
    scored = sorted(
        ((s, r) for r in rows if (s := _score(r, terms, phrase)) > 0),
        key=lambda pair: (-pair[0], pair[1].relative_path),
    )[:top_k]

    if not scored:
        ctx.status = "error"
        return error(_no_match(project, query, doc_type))

    ctx.top_result_path = scored[0][1].relative_path
    ctx.top_result_score = round(scored[0][0], 2)
    return text(_render_results(project, query, scored, terms))


__all__ = ["DEFINITIONS", "NAME", "dispatch"]
