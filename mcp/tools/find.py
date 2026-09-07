"""tools/find.py - the playbook_find MCP tool.

One discovery tool, two modes:

 - No `query`  → list mode: every doc in the project with type, title, summary,
   and `triggers:` phrases.
 - With `query` → search mode: BM25 over the corpus, ranked snippets.

Only search mode writes `ctx.query` / `ctx.top_result_*`, so browsing the
catalogue does not pollute the dashboard's search analytics.
"""

from __future__ import annotations

from mcp.types import TextContent, Tool

from loader import KNOWN_DOC_TYPES, RuleDoc, RulesStore
from search import RulesSearchEngine

from .common import (
    PROJECT_PARAM_DESC,
    canonical_project,
    fail,
    summary,
    text,
    title,
    triggers,
)

DEFINITIONS: list[Tool] = [
    Tool(
        name="playbook_find",
        description=(
            "Discover docs. With `query`: keyword search returning ranked "
            "snippets with source path and parent heading - use for "
            "cross-cutting questions or when you don't know which doc to "
            "fetch. Without `query`: a catalogue of every doc with type, "
            "title, summary and trigger phrases. Fetch a result with "
            "playbook_get."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": PROJECT_PARAM_DESC,
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Optional - keywords or natural language. Omit to list "
                        "all docs instead of searching."
                    ),
                },
                "type": {
                    "type": "string",
                    "description": 'Optional - restrict to a doc type (e.g. "pattern").',
                    "enum": list(KNOWN_DOC_TYPES),
                },
                "top_k": {
                    "type": "integer",
                    "description": "Search mode only. Max results. Default: 10.",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": ["project"],
        },
    ),
]

_NAMES = {t.name for t in DEFINITIONS}


def _entry(doc: RuleDoc) -> str:
    """One catalogue line."""
    head = f"- **{doc.relative_path}** ({doc.doc_type}) - *{title(doc)}*"
    body = summary(doc)
    if body:
        head += f"\n  {body}"
    trig = triggers(doc)
    if trig:
        head += "\n  _Triggers:_ " + ", ".join(f"`{t}`" for t in trig)
    return head


async def dispatch(
    name: str,
    arguments: dict,
    ctx: object,
    store: RulesStore,
    engine: RulesSearchEngine,
) -> list[TextContent] | None:
    if name not in _NAMES:
        return None

    project_raw = (arguments.get("project") or "").strip()
    query = (arguments.get("query") or "").strip()
    doc_type = arguments.get("type") or None

    known = store.projects()
    project = canonical_project(project_raw, known)
    if project is None:
        listing = "\n".join(f"- {p}" for p in known) or "(none)"
        return fail(
            ctx,
            "not_found",
            f"Project '{project_raw}' not found. "
            "`project` must be the basename of your current workspace "
            f"directory. Available:\n{listing}",
        )

    if not query:
        return _list_mode(ctx, store, project, doc_type)
    return _search_mode(ctx, engine, project, query, doc_type, arguments.get("top_k", 10))


def _list_mode(
    ctx: object,
    store: RulesStore,
    project: str,
    doc_type: str | None,
) -> list[TextContent]:
    docs = store.of_type(project, doc_type) if doc_type else store.for_project(project)

    if not docs:
        type_clause = f" of type '{doc_type}'" if doc_type else ""
        return fail(ctx, "empty", f"No docs{type_clause} for project '{project}'.")

    ordered = sorted(docs, key=lambda d: (d.doc_type, d.relative_path))
    return text("\n".join(_entry(d) for d in ordered))


def _search_mode(
    ctx: object,
    engine: RulesSearchEngine,
    project: str,
    query: str,
    doc_type: str | None,
    raw_top_k: object,
) -> list[TextContent]:
    try:
        top_k = int(raw_top_k)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        top_k = 10
    top_k = max(1, min(50, top_k))

    ctx.query = query
    results = engine.search(query=query, project=project, doc_type=doc_type, top_k=top_k)

    if not results:
        return fail(ctx, "empty", f"No results found for query: '{query}'")

    top = results[0]
    ctx.top_result_path = f"{top.project}/{top.relative_path}"
    ctx.top_result_score = top.score

    lines = []
    for i, r in enumerate(results, 1):
        heading_str = f" - under heading: *{r.heading}*" if r.heading else ""
        lines.append(
            f"### {i}. {r.project}/{r.relative_path}  "
            f"(type: {r.doc_type}, score: {r.score}){heading_str}\n\n"
            f"{r.snippet}\n"
        )
    return text("\n---\n".join(lines))
