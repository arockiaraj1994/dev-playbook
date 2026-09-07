"""tools/start.py - the playbook_start MCP tool.

One entry point for both kinds of work:

  mode="code"  (default)  the canonical first call for any coding task -
                          identity, always-on rules, the linked requirement,
                          the matched workflow, and one Next Calls list.
  mode="prd"|"story"      the authoring bootstrap - template, next free id,
                          glossary, architecture, guardrails, the matched
                          write-* workflow, and suggested `targets:`.

The guardrails block and the requirement bundle are produced by
`get.render_ref()`, the same function behind playbook_get. Before v0.8.0 both
were re-implemented here and drifted; composing the renderer is what keeps
`playbook_start` and `playbook_get` byte-identical rather than merely similar.
"""

from __future__ import annotations

import re
from pathlib import Path

from mcp.types import TextContent, Tool

from loader import RuleDoc, RulesStore
from refs import Ref, RefError, parse_ref
from search import RulesSearchEngine

from .common import (
    PROJECT_PARAM_DESC,
    fail,
    get_doc,
    meta_str,
    next_calls_lines,
    render_next_calls,
    resolve_project,
    text,
)
from .get import DocNotFound, render_ref

_MCP_DIR = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _MCP_DIR / "templates"

_MODES = ("code", "prd", "story")

DEFINITIONS: list[Tool] = [
    Tool(
        name="playbook_start",
        description=(
            "Entry point - call this before other playbook tools. "
            'mode="code" (default) returns one bundle for a coding task: '
            "project identity, always-on guardrails and definition of done, "
            "the linked requirement when `ref` is given, the workflow matched "
            "to `intent`, and a Next Calls list of follow-up playbook_get "
            'calls. mode="prd" or "story" returns the authoring bootstrap '
            "instead: template, next free id, glossary, architecture, "
            "guardrails and suggested targets."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": PROJECT_PARAM_DESC,
                },
                "intent": {
                    "type": "string",
                    "description": (
                        "What the user asked for, as a free-form sentence "
                        "(e.g. 'fix a bug in the SFTP route', or for authoring "
                        "'offline sync for saved articles')."
                    ),
                },
                "mode": {
                    "type": "string",
                    "description": (
                        "code (default) for a coding task; prd or story to author a requirement."
                    ),
                    "enum": list(_MODES),
                    "default": "code",
                },
                "ref": {
                    "type": "string",
                    "description": (
                        'mode=code: requirement to include, e.g. "req:ST-114" '
                        "(a story arrives with its parent PRD summary; a PRD "
                        "with its story list). mode=story: the parent PRD, "
                        'e.g. "req:PRD-003". Bare ids are accepted.'
                    ),
                },
            },
            "required": ["project", "intent"],
        },
    ),
]

_NAMES = {t.name for t in DEFINITIONS}


def _as_requirement_ref(raw: str) -> Ref:
    """Parse a requirement ref, accepting a bare id (`ST-101` → `req:ST-101`)."""
    candidate = raw.strip()
    if candidate and ":" not in candidate:
        candidate = f"req:{candidate}"
    return parse_ref(candidate)


# ---------------------------------------------------------------------------
# Workflow matching
# ---------------------------------------------------------------------------


def _match_workflow(
    store: RulesStore, engine: RulesSearchEngine, project: str, intent: str
) -> RuleDoc | None:
    """Pick the most relevant workflow doc for `intent`.

    1. Exact-ish match against frontmatter `triggers:` (case-insensitive
       substring match in either direction).
    2. Fallback to BM25 search restricted to doc_type=workflow.
    """
    intent_lc = intent.lower()
    workflows = store.of_type(project, "workflow", corpus="standards")

    for wf in workflows:
        trig = wf.metadata.get("triggers") or []
        if not isinstance(trig, list):
            continue
        for t in trig:
            if not isinstance(t, str) or not t.strip():
                continue
            phrase = t.strip().lower()
            if phrase in intent_lc or intent_lc in phrase:
                return wf

    if not workflows:
        return None
    results = engine.search(
        query=intent, project=project, doc_type="workflow", top_k=1, corpus="standards"
    )
    if not results:
        return None
    return store.get(project, results[0].relative_path, corpus="standards")


# The IDENTITY block runs to the next H2, or to EOF if it is the last section.
_IDENTITY_RE = re.compile(
    r"^##\s+IDENTITY\b.*?(?=^##\s|\Z)", re.MULTILINE | re.DOTALL | re.IGNORECASE
)


def _identity_section(agents: RuleDoc | None, max_chars: int = 1200) -> str:
    """The `## IDENTITY` block of AGENTS.md, or "" if absent."""
    if not agents:
        return ""
    match = _IDENTITY_RE.search(agents.content)
    if not match:
        return ""
    body = match.group(0).strip().rstrip("-").strip()
    if not body:
        return ""
    if len(body) > max_chars:
        body = (
            body[:max_chars].rstrip()
            + '\n\n_(truncated - call playbook_get(ref="agents") for the rest)_'
        )
    return body


def _depends_warnings(store: RulesStore, story: RuleDoc) -> list[str]:
    raw = story.metadata.get("depends_on") or []
    if not isinstance(raw, list):
        return []
    warnings: list[str] = []
    for dep in raw:
        if not isinstance(dep, str) or not dep.strip():
            continue
        dep_id = dep.strip()
        other = store.find_by_id("requirements", story.project, dep_id)
        if other is None:
            warnings.append(f"⚠ {story.name} depends on {dep_id} (not found).")
            continue
        status = meta_str(other, "status", "draft")
        if status != "shipped":
            warnings.append(f"⚠ {story.name} depends on {dep_id} (status: {status}).")
    return warnings


# ---------------------------------------------------------------------------
# mode=code
# ---------------------------------------------------------------------------


def _requirement_section(
    store: RulesStore, project: str, ref: Ref, ctx: object
) -> tuple[str, list[str]]:
    """Requirement block for the bundle, plus the story's `targets:` bullets.

    The body is `get.render_ref()` verbatim - the same bytes
    playbook_get(ref="req:…") returns. Only the warnings above it are specific
    to starting work.
    """
    body = render_ref(store, project, ref, ctx)  # raises DocNotFound

    doc = store.find_by_id("requirements", project, ref.name)
    prefix = ""
    target_lines: list[str] = []
    if doc is not None:
        if meta_str(doc, "status", "draft") == "shipped":
            prefix += (
                f"> ⚠ **Warning:** `{doc.name}` has status `shipped` - "
                "treat as frozen historical context, not an active handoff.\n\n"
            )
        if doc.doc_type == "story":
            warnings = _depends_warnings(store, doc)
            if warnings:
                prefix += "\n".join(warnings) + "\n\n"
            target_lines = next_calls_lines(doc, project, key="targets")

    return prefix + body, target_lines


def _code_bundle(
    store: RulesStore,
    engine: RulesSearchEngine,
    ctx: object,
    project: str,
    intent: str,
    ref: Ref | None,
) -> list[TextContent]:
    parts: list[str] = [f"# playbook_start - {project}\n\n_Task:_ {intent}\n"]

    identity = _identity_section(get_doc(store, project, "AGENTS.md"))
    if identity:
        parts.append(identity)

    try:
        parts.append("## Always-on rules\n\n" + render_ref(store, project, parse_ref("guardrails")))
    except DocNotFound:
        parts.append(
            "_No core/guardrails.md or core/definition-of-done.md found - "
            "ask the project maintainer to add them._"
        )

    target_lines: list[str] = []
    if ref is not None:
        try:
            requirement_md, target_lines = _requirement_section(store, project, ref, ctx)
        except DocNotFound as exc:
            return fail(ctx, "not_found", str(exc))
        parts.append("## Requirement\n\n" + requirement_md)

    # The workflow body is embedded raw rather than via render_ref: its
    # `see_also:` bullets are merged into the single Next Calls section at the
    # end of the bundle instead of appearing inline mid-document.
    workflow = _match_workflow(store, engine, project, intent)
    if workflow:
        ctx.doc_path = f"{project}/{workflow.relative_path}"
        parts.append(
            f"## Matched workflow: `{workflow.name}`\n\n"
            f"_Path:_ `{workflow.relative_path}`\n\n" + workflow.content.strip()
        )
    else:
        parts.append(
            "## Matched workflow\n\n"
            f'_No workflow matched. Try `playbook_find(project="{project}")` '
            "to list the available docs._"
        )

    # One consolidated Next Calls at the very end: story `targets:` first (the
    # WHAT→HOW bridge), then the workflow's `see_also:`, de-duplicated.
    next_lines: list[str] = list(target_lines)
    if workflow:
        for line in next_calls_lines(workflow, project):
            if line not in next_lines:
                next_lines.append(line)
    parts.append(render_next_calls(next_lines).lstrip("\n"))

    return text("\n\n".join(p for p in parts if p) + "\n")


# ---------------------------------------------------------------------------
# mode=prd | story
# ---------------------------------------------------------------------------


def _next_id(store: RulesStore, project: str, mode: str) -> str:
    """Scan existing ids and return the next free PRD-NNN or ST-NNN."""
    prefix = "PRD-" if mode == "prd" else "ST-"
    nums: list[int] = []
    for doc in store.all_docs(corpus="requirements"):
        if doc.project != project or doc.doc_type not in ("prd", "story"):
            continue
        rid = meta_str(doc, "id") or doc.name
        if rid.startswith(prefix):
            try:
                nums.append(int(rid[len(prefix) :]))
            except ValueError:
                continue
    nxt = (max(nums) + 1) if nums else (1 if mode == "prd" else 100)
    return f"{prefix}{nxt:03d}" if mode == "prd" else f"{prefix}{nxt}"


def _load_template(mode: str) -> str:
    name = "PRD.md" if mode == "prd" else "STORY.md"
    path = _TEMPLATES_DIR / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return f"# {mode.upper()} template missing at mcp/templates/{name}\n"


def _suggest_targets(
    engine: RulesSearchEngine, project: str, intent: str, top_k: int = 5
) -> list[str]:
    """Standards refs worth listing in the new doc's `targets:` frontmatter."""
    results = engine.search(query=intent, project=project, corpus="standards", top_k=top_k)
    suggestions: list[str] = []
    for r in results:
        stem = Path(r.relative_path).stem
        if r.doc_type == "pattern":
            suggestions.append(f"pattern:{stem}")
        elif r.doc_type == "skill":
            suggestions.append(f"skill:{stem}")
        elif r.doc_type == "workflow":
            suggestions.append(f"workflow:{stem}")
        elif r.doc_type == "language-rules":
            # languages/kotlin/testing.md → language:kotlin/testing
            parts = Path(r.relative_path).parts
            if len(parts) == 3:
                suggestions.append(f"language:{parts[1]}/{Path(parts[2]).stem}")
    return list(dict.fromkeys(suggestions))


def _authoring_bundle(
    store: RulesStore,
    engine: RulesSearchEngine,
    ctx: object,
    project: str,
    intent: str,
    mode: str,
    parent: Ref | None,
) -> list[TextContent]:
    if mode == "story" and parent is None:
        return fail(
            ctx,
            "error",
            '`ref` (the parent PRD, e.g. "req:PRD-003") is required when mode="story".',
        )

    parent_id = parent.name if parent is not None else ""
    if mode == "story":
        prd = store.find_by_id("requirements", project, parent_id)
        if not prd or prd.doc_type != "prd":
            return fail(
                ctx,
                "not_found",
                f"Parent PRD '{parent_id}' not found in {project}. "
                f'Call playbook_find(project="{project}", corpus="requirements", type="prd").',
            )

    next_id = _next_id(store, project, mode)
    ctx.requirement_id = next_id

    parts: list[str] = [
        f"# playbook_start ({mode}) - {project}\n\n"
        f"_Intent:_ {intent}  \n"
        f"_Type:_ {mode}  \n"
        f"_Next id:_ **`{next_id}`**"
        + (f"  \n_Parent PRD:_ `{parent_id}`" if parent_id else "")
        + "\n",
        f"## Template\n\n```markdown\n{_load_template(mode).rstrip()}\n```",
    ]

    glossary = get_doc(store, project, "core/glossary.md")
    overview = get_doc(store, project, "architecture/overview.md")
    guard = get_doc(store, project, "core/guardrails.md")
    if glossary:
        parts.append("## Glossary\n\n" + glossary.content.strip())
    if overview:
        parts.append("## Architecture overview\n\n" + overview.content.strip())
    if guard:
        parts.append(
            "## Guardrails (do not spec anything these forbid)\n\n" + guard.content.strip()
        )

    wf_name = "write-prd" if mode == "prd" else "write-story"
    workflow = store.get(project, f"workflows/{wf_name}.md", corpus="requirements")
    if workflow:
        parts.append(f"## Matched workflow: `{workflow.name}`\n\n" + workflow.content.strip())
    else:
        parts.append(
            "## Matched workflow\n\n"
            f"_No requirements/{project}/workflows/{wf_name}.md yet - "
            "create it so PMs get a consistent authoring flow._"
        )

    suggestions = _suggest_targets(engine, project, intent)
    if suggestions:
        parts.append(
            "## Suggested `targets:` (edit before committing)\n\n"
            f"`targets: [{', '.join(suggestions)}]`\n\n"
            "These came from a search over standards - keep what fits, drop the rest."
        )
    else:
        parts.append(
            "## Suggested `targets:`\n\n"
            "_No strong matches - call "
            f'`playbook_find(project="{project}", query="<keywords>")` '
            "to find candidates._"
        )

    parts.append(
        "---\n\n## Next Calls\n\n"
        f'- `playbook_find(project="{project}", query="{intent}")` - find more targets\n'
        f'- `playbook_find(project="{project}", corpus="requirements")` '
        "- see existing PRDs/stories\n"
    )

    return text("\n\n".join(parts) + "\n")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def dispatch(
    name: str,
    arguments: dict,
    ctx: object,
    store: RulesStore,
    engine: RulesSearchEngine,
) -> list[TextContent] | None:
    if name not in _NAMES:
        return None

    intent = (arguments.get("intent") or "").strip()
    if not intent:
        return fail(ctx, "error", "`intent` is required.")

    mode = (arguments.get("mode") or "code").strip()
    if mode not in _MODES:
        return fail(ctx, "error", f"`mode` must be one of {list(_MODES)}; got '{mode}'.")

    raw_ref = (arguments.get("ref") or "").strip()
    ref: Ref | None = None
    if raw_ref:
        try:
            ref = _as_requirement_ref(raw_ref)
        except RefError as exc:
            return fail(ctx, "error", str(exc))
        if ref.kind != "req":
            return fail(
                ctx,
                "error",
                f'`ref` on playbook_start names a requirement, e.g. "req:ST-101"; got "{raw_ref}".',
            )

    resolution = resolve_project(store, arguments.get("project"), corpus="standards")
    if not resolution.ok:
        return fail(ctx, resolution.status, resolution.error_text or "Project resolution failed.")
    project = resolution.project
    assert project is not None

    ctx.query = intent
    if ref is not None:
        ctx.requirement_id = ref.name

    if mode == "code":
        return _code_bundle(store, engine, ctx, project, intent, ref)
    return _authoring_bundle(store, engine, ctx, project, intent, mode, ref)
