"""tools/start.py - the playbook_start MCP tool.

The canonical first call for any coding task: identity, always-on rules, the
matched workflow, and one consolidated Next Calls list.

The guardrails block is produced by `get.render_ref()`, the same function behind
playbook_get. Before v0.8.0 it was re-implemented here and the two drifted;
composing the renderer is what keeps them byte-identical rather than merely
similar.
"""

from __future__ import annotations

import re

from mcp.types import TextContent, Tool

from loader import RuleDoc, RulesStore
from refs import parse_ref
from search import RulesSearchEngine

from .common import (
    PROJECT_PARAM_DESC,
    fail,
    get_doc,
    next_calls_lines,
    render_next_calls,
    resolve_project,
    text,
)
from .get import DocNotFound, render_ref

DEFINITIONS: list[Tool] = [
    Tool(
        name="playbook_start",
        description=(
            "Entry point for any coding task - call this before other playbook "
            "tools. Returns one bundle: project identity, always-on guardrails "
            "and definition of done, the workflow matched to `intent`, and a "
            "Next Calls list of follow-up playbook_get calls."
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
                        "(e.g. 'fix a bug in the SFTP route' or "
                        "'add a new connector for Quarkus')."
                    ),
                },
            },
            "required": ["project", "intent"],
        },
    ),
]

_NAMES = {t.name for t in DEFINITIONS}


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
    workflows = store.of_type(project, "workflow")

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
    results = engine.search(query=intent, project=project, doc_type="workflow", top_k=1)
    if not results:
        return None
    return store.get(project, results[0].relative_path)


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

    resolution = resolve_project(store, arguments.get("project"))
    if not resolution.ok:
        return fail(ctx, resolution.status, resolution.error_text or "Project resolution failed.")
    project = resolution.project
    assert project is not None

    ctx.query = intent

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

    next_lines: list[str] = []
    if workflow:
        for line in next_calls_lines(workflow, project):
            if line not in next_lines:
                next_lines.append(line)
    parts.append(render_next_calls(next_lines).lstrip("\n"))

    return text("\n\n".join(p for p in parts if p) + "\n")
