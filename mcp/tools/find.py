"""tools/find.py - the playbook_find MCP tool.

One discovery tool, two modes:

 - No `query`  → list mode: every doc in the project with type, title, summary,
   and `triggers:` phrases (requirements also show status and priority).
 - With `query` → search mode: BM25 across the corpus, ranked snippets.

This replaces the v0.7.0 pair playbook_search_docs + playbook_list_requirements,
which rendered near-identical listings over the same store; the only capability
unique to the latter was its `status` / `prd` filters, which are parameters here.

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
    meta_str,
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
            "title, summary and trigger phrases. Filter with type=, status=, "
            "prd=. Searches standards by default; set corpus=requirements or "
            "all to include PRDs/stories. Fetch a result with playbook_get."
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
                    "description": 'Optional - restrict to a doc type (e.g. "pattern", "story").',
                    "enum": list(KNOWN_DOC_TYPES),
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "approved", "shipped"],
                    "description": "Optional - requirements only; filter by status.",
                },
                "prd": {
                    "type": "string",
                    "description": "Optional - requirements only; restrict to stories under this PRD id.",
                },
                "corpus": {
                    "type": "string",
                    "description": "Which corpus to search. Default: standards.",
                    "enum": ["standards", "requirements", "all"],
                    "default": "standards",
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

_REQUIREMENT_TYPES = frozenset({"prd", "story"})


def _entry(doc: RuleDoc) -> str:
    """One catalogue line. Requirements carry status/priority, standards don't."""
    if doc.doc_type in _REQUIREMENT_TYPES:
        priority = meta_str(doc, "priority")
        pri = f" · {priority}" if priority else ""
        head = (
            f"- **{doc.name}** ({doc.doc_type}, "
            f"{meta_str(doc, 'status', 'draft')}{pri}) - *{title(doc)}*"
        )
    else:
        head = f"- **{doc.relative_path}** ({doc.doc_type}, {doc.corpus}) - *{title(doc)}*"
    body = summary(doc)
    if body:
        head += f"\n  {body}"
    trig = triggers(doc)
    if trig:
        head += "\n  _Triggers:_ " + ", ".join(f"`{t}`" for t in trig)
    return head


def _apply_requirement_filters(
    docs: list[RuleDoc],
    store: RulesStore,
    project: str,
    status: str | None,
    prd: str | None,
) -> tuple[list[RuleDoc], str | None]:
    """Filter by requirement status / parent PRD. Returns (docs, error)."""
    if status:
        docs = [d for d in docs if meta_str(d, "status", "draft") == status]
    if prd:
        parent = store.find_by_id("requirements", project, prd)
        if not parent or parent.doc_type != "prd":
            return [], (
                f"PRD '{prd}' not found in {project}. "
                f'Call playbook_find(project="{project}", corpus="requirements", type="prd").'
            )
        allowed = {s.relative_path for s in store.stories_of(parent)}
        docs = [d for d in docs if d.relative_path in allowed]
    return docs, None


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
    status = (arguments.get("status") or "").strip() or None
    prd = (arguments.get("prd") or "").strip() or None
    corpus = (arguments.get("corpus") or "standards").strip()
    if corpus not in ("standards", "requirements", "all"):
        corpus = "standards"

    known = list(store.projects(corpus=None))
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

    # status/prd only make sense against the requirements corpus.
    if (status or prd) and corpus == "standards":
        corpus = "requirements"

    ctx.corpus = corpus

    if not query:
        return _list_mode(ctx, store, project, doc_type, corpus, status, prd)
    return _search_mode(
        ctx,
        store,
        engine,
        project,
        query,
        doc_type,
        arguments.get("top_k", 10),
        corpus,
        status,
        prd,
    )


def _list_mode(
    ctx: object,
    store: RulesStore,
    project: str,
    doc_type: str | None,
    corpus: str,
    status: str | None,
    prd: str | None,
) -> list[TextContent]:
    corpus_filter: str | None = None if corpus == "all" else corpus
    if doc_type:
        docs = list(store.of_type(project, doc_type, corpus=corpus_filter))
    else:
        docs = list(store.for_project(project, corpus=corpus_filter))

    if status or prd:
        docs, err = _apply_requirement_filters(docs, store, project, status, prd)
        if err:
            return fail(ctx, "not_found", err)

    if not docs:
        type_clause = f" of type '{doc_type}'" if doc_type else ""
        return fail(
            ctx,
            "empty",
            f"No docs{type_clause} for project '{project}' (corpus={corpus}).",
        )

    ordered = sorted(docs, key=lambda d: (d.corpus, d.doc_type, d.relative_path))
    return text("\n".join(_entry(d) for d in ordered))


def _search_mode(
    ctx: object,
    store: RulesStore,
    engine: RulesSearchEngine,
    project: str,
    query: str,
    doc_type: str | None,
    raw_top_k: object,
    corpus: str,
    status: str | None,
    prd: str | None,
) -> list[TextContent]:
    try:
        top_k = int(raw_top_k)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        top_k = 10
    top_k = max(1, min(50, top_k))

    ctx.query = query
    results = engine.search(
        query=query,
        project=project,
        doc_type=doc_type,
        top_k=top_k,
        corpus=corpus,
    )

    if status or prd:
        # Resolve each hit back to its doc so the requirement filters apply to
        # search results too, rather than being silently ignored.
        hits = {(r.project, r.relative_path): r for r in results}
        docs = [
            d
            for (p, rel), _ in hits.items()
            if (d := store.get(p, rel, corpus="requirements")) is not None
        ]
        docs, err = _apply_requirement_filters(docs, store, project, status, prd)
        if err:
            return fail(ctx, "not_found", err)
        keep = {(d.project, d.relative_path) for d in docs}
        results = [r for r in results if (r.project, r.relative_path) in keep]

    if not results:
        return fail(ctx, "empty", f"No results found for query: '{query}'")

    top = results[0]
    ctx.top_result_path = f"{top.project}/{top.relative_path}"
    ctx.top_result_score = top.score

    lines = []
    for i, r in enumerate(results, 1):
        heading_str = f" - under heading: *{r.heading}*" if r.heading else ""
        lines.append(
            f"### {i}. [{r.corpus}] {r.project}/{r.relative_path}  "
            f"(type: {r.doc_type}, score: {r.score}){heading_str}\n\n"
            f"{r.snippet}\n"
        )
    return text("\n---\n".join(lines))
