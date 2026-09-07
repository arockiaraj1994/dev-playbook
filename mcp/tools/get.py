"""tools/get.py - the playbook_get MCP tool.

`playbook_get(project, ref)` fetches exactly one doc, addressed by the `ref`
grammar in tools/refs.py - the same grammar the corpus uses in `see_also:` and
`targets:` frontmatter.

`render_ref()` is the single renderer for every doc body this server returns.
playbook_start composes it rather than re-implementing it, so a guardrails block,
a workflow body or a requirement bundle is the same bytes wherever it appears.
"""

from __future__ import annotations

import logging
import re

from mcp.types import TextContent, Tool

from loader import RuleDoc, RulesStore, resolve_rules_root
from refs import REF_KINDS, Ref, RefError, parse_ref, relative_path
from search import RulesSearchEngine

from .common import (
    PROJECT_PARAM_DESC,
    fail,
    get_doc,
    meta_str,
    next_calls_section,
    resolve_project,
    summary,
    text,
    title,
)

logger = logging.getLogger(__name__)

DEFINITIONS: list[Tool] = [
    Tool(
        name="playbook_get",
        description=(
            "Fetch one doc by `ref`. A ref is the same string the corpus uses "
            'in see_also:/targets: frontmatter - "guardrails", '
            '"pattern:repository", "language:kotlin/testing", "workflow:bug-fix", '
            '"req:ST-101" - so a Next Calls bullet can be followed verbatim. '
            "Returns the doc body plus its own Next Calls. To discover refs, "
            "use playbook_find."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": PROJECT_PARAM_DESC,
                },
                "ref": {
                    "type": "string",
                    "description": (
                        "Doc address. Bare kind for singletons: agents, "
                        "guardrails, architecture, gate. `kind:name` otherwise: "
                        "architecture:<adr>, language:<lang>[/standards|testing|"
                        "anti-patterns], pattern:<name>, skill:<name>, "
                        "workflow:<name>, gate:<script>, req:<PRD-001|ST-101>. "
                        f"Kinds: {', '.join(REF_KINDS)}."
                    ),
                },
            },
            "required": ["project", "ref"],
        },
    ),
]

_NAMES = {t.name for t in DEFINITIONS}

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class DocNotFound(Exception):
    """A ref parsed cleanly but names no doc on disk."""


# ---------------------------------------------------------------------------
# Requirement rendering (shared with playbook_start)
# ---------------------------------------------------------------------------


def _extract_sections(content: str, headings: list[str]) -> str:
    """Return named ## sections from content (case-insensitive heading match)."""
    wanted = {h.lower() for h in headings}
    parts = _SECTION_RE.split(content)
    out: list[str] = []
    i = 1
    while i + 1 < len(parts):
        heading, body = parts[i], parts[i + 1]
        if heading.strip().lower() in wanted:
            out.append(f"## {heading.strip()}\n\n{body.strip()}")
        i += 2
    return "\n\n".join(out)


def prd_summary(prd: RuleDoc) -> str:
    sections = _extract_sections(prd.content, ["Problem", "Non-Goals"])
    return (
        f"### Parent PRD: `{prd.name}` - {title(prd)} ({meta_str(prd, 'status', 'draft')})\n\n"
        f"_Description:_ {summary(prd, 160)}\n\n"
        + (sections if sections else "_(no Problem / Non-Goals sections)_")
    )


def find_requirement(store: RulesStore, req_id: str, project: str | None) -> RuleDoc | None:
    req_id = req_id.strip()
    projects = [project] if project else list(store.projects(corpus="requirements"))
    for p in projects:
        doc = store.find_by_id("requirements", p, req_id)
        if doc:
            return doc
    return None


def _render_requirement(store: RulesStore, project: str, ref: Ref) -> tuple[str, RuleDoc]:
    """A PRD with its story list, or a story with its parent PRD summary.

    There is no `depth` switch: the related context is always what the caller
    wants, and pre-0.8.0 `start_task(requirement=)` already included it
    unconditionally while `get_doc` defaulted it off.
    """
    doc = find_requirement(store, ref.name, project) or find_requirement(store, ref.name, None)
    if not doc:
        raise DocNotFound(
            f"Requirement '{ref.name}' not found in '{project}'. "
            f'Call playbook_find(project="{project}", corpus="requirements") '
            "to see what exists."
        )

    parts: list[str] = [
        f"# {doc.name} - {title(doc)}\n\n"
        f"_Status:_ {meta_str(doc, 'status', 'draft')}  \n"
        f"_Path:_ `{doc.project}/{doc.relative_path}`\n\n" + doc.content.strip()
    ]

    if doc.doc_type == "prd":
        stories = store.stories_of(doc)
        if stories:
            lines = [
                f"- **{s.name}** ({meta_str(s, 'status', 'draft')}"
                f"{(' · ' + meta_str(s, 'priority')) if meta_str(s, 'priority') else ''})"
                f" - {title(s)}"
                for s in stories
            ]
            parts.append("## Stories\n\n" + "\n".join(lines))
        else:
            parts.append("## Stories\n\n_No stories yet._")
    elif doc.doc_type == "story":
        prd = store.prd_of(doc)
        if prd:
            parts.append("## Parent PRD (summary)\n\n" + prd_summary(prd))

    next_calls = next_calls_section(doc, doc.project, key="targets").lstrip("\n")
    if next_calls:
        parts.append(next_calls)

    return "\n\n".join(parts) + "\n", doc


# ---------------------------------------------------------------------------
# Standards rendering
# ---------------------------------------------------------------------------


def _render_guardrails(store: RulesStore, project: str) -> str:
    guard = get_doc(store, project, "core/guardrails.md")
    dod = get_doc(store, project, "core/definition-of-done.md")
    if not guard and not dod:
        raise DocNotFound(
            f"No core/guardrails.md or core/definition-of-done.md for project '{project}'."
        )
    parts: list[str] = []
    if guard:
        parts.append("# Guardrails (always-on)\n\n" + guard.content.strip())
    if dod:
        parts.append("# Definition of Done\n\n" + dod.content.strip())
    return "\n\n---\n\n".join(parts)


def _render_gate(store: RulesStore, project: str, ref: Ref) -> str:
    gate = get_doc(store, project, "gates/README.md")
    if not gate:
        raise DocNotFound(f"No gates/README.md for project '{project}'.")
    scripts = gate.metadata.get("gate_scripts") or []

    if not ref.name:
        listing = (
            "\n\n## Available scripts\n\n" + "\n".join(f"- `gates/scripts/{s}`" for s in scripts)
            if scripts
            else "\n\n_No executable scripts under gates/scripts/._\n"
        )
        return gate.content + listing

    script = ref.name if ref.name.endswith(".sh") else f"{ref.name}.sh"
    if script not in scripts:
        raise DocNotFound(
            f"Gate script '{script}' not found for project '{project}'. "
            f"Available: {scripts or '[]'}."
        )
    return (
        f"# Gate: {script}\n\n"
        f"Path: `{project}/gates/scripts/{script}`\n\n"
        "The MCP server does not execute gate scripts. To run it locally:\n\n"
        f"```\nbash {project}/gates/scripts/{script}\n```\n"
        + _script_preview(project, script)
        + "\nSee the gate README for what this script enforces:\n\n"
        + gate.content
    )


def _script_preview(project: str, script_filename: str, max_lines: int = 40) -> str:
    """First lines of a gate script, for the agent to read (never executed).

    `script_filename` is already validated against the `gate_scripts` directory
    listing by the caller, so it cannot escape gates/scripts/.
    """
    path = resolve_rules_root() / project / "gates" / "scripts" / script_filename
    try:
        with path.open(encoding="utf-8") as fh:
            lines = [next(fh, None) for _ in range(max_lines)]
    except OSError as exc:
        logger.warning("Could not read gate script %s: %s", path, exc)
        return ""
    body = "".join(line for line in lines if line is not None).rstrip()
    if not body:
        return ""
    truncated = "\n… (truncated)" if lines[-1] is not None else ""
    return f"\n## First {max_lines} lines\n\n```bash\n{body}{truncated}\n```\n"


# ---------------------------------------------------------------------------
# The single renderer
# ---------------------------------------------------------------------------


def render_ref(store: RulesStore, project: str, ref: Ref, ctx: object | None = None) -> str:
    """Render the doc a ref names. Raises DocNotFound.

    Every doc body this server emits comes through here - playbook_get returns
    it directly and playbook_start embeds it.
    """
    if ref.kind == "req":
        body, doc = _render_requirement(store, project, ref)
        if ctx is not None:
            ctx.doc_path = f"{doc.corpus}/{doc.project}/{doc.relative_path}"
            ctx.requirement_id = doc.name
            ctx.corpus = "requirements"
        return body

    if ref.kind == "guardrails":
        if ctx is not None:
            ctx.doc_path = f"{project}/core/guardrails.md+definition-of-done.md"
        return _render_guardrails(store, project)

    if ref.kind == "gate":
        if ctx is not None:
            ctx.doc_path = (
                f"{project}/gates/scripts/{ref.name}.sh"
                if ref.name
                else f"{project}/gates/README.md"
            )
        return _render_gate(store, project, ref)

    rel = relative_path(ref)
    assert rel is not None  # every remaining kind has a static path
    if ctx is not None:
        ctx.doc_path = f"{project}/{rel}"
    doc = get_doc(store, project, rel)
    if not doc:
        raise DocNotFound(
            f"{ref.label} not found for project '{project}' (expected at {rel}). "
            f'Call playbook_find(project="{project}") to see what is available.'
        )
    return doc.content + next_calls_section(doc, project)


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

    try:
        ref = parse_ref(arguments.get("ref") or "")
    except RefError as exc:
        return fail(ctx, "error", str(exc))

    resolution = resolve_project(store, arguments.get("project"), corpus=ref.corpus)
    if not resolution.ok and ref.corpus == "requirements":
        # Requirement ids live under requirements/ but agents pass the standards
        # project name (they are the same slug).
        resolution = resolve_project(store, arguments.get("project"), corpus="standards")
    if not resolution.ok:
        return fail(ctx, resolution.status, resolution.error_text or "Project not found.")
    project = resolution.project
    assert project is not None

    try:
        return text(render_ref(store, project, ref, ctx))
    except DocNotFound as exc:
        return fail(ctx, "not_found", str(exc))
