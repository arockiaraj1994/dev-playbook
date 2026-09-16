"""Tests for the per-artifact read tools and playbook_find_standards.

Surface: playbook_get_agents / _guardrails / _standards / _patterns / _workflow
/ _gates, plus playbook_find_standards. The old playbook_get_standard (ref
grammar) and playbook_start_task were retired when the surface split per family.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

import scaffold_service
from standards_store import StandardsStore
from tools import agents, find, gates, guardrails, patterns, standards, workflow

JAVA = {"package": "com.acme.billing"}


@dataclass
class Ctx:
    status: str = "ok"
    query: str | None = None
    doc_path: str | None = None
    top_result_path: str | None = None
    top_result_score: float | None = None


@pytest.fixture
async def store(tmp_path: Path) -> StandardsStore:
    s = StandardsStore(tmp_path / "standards.db")
    await s.init()
    await scaffold_service.scaffold_project(
        s, languages=["java"], project="billing", placeholders=JAVA
    )
    return s


async def run(module, store: StandardsStore, **args) -> tuple[str, Ctx]:
    ctx = Ctx()
    result = await module.dispatch(module.NAME, args, ctx, store)
    assert result is not None
    return result[0].text, ctx


# ---------------------------------------------------------------------------
# playbook_get_agents
# ---------------------------------------------------------------------------


async def test_agents_returns_identity_and_context(store: StandardsStore):
    body, ctx = await run(agents, store, project="billing")
    assert ctx.status == "ok"
    assert "AGENTS.md" in body
    assert "ARCHITECTURE.md" in body
    assert "glossary.md" in body


async def test_agents_case_insensitive_project(store: StandardsStore):
    _body, ctx = await run(agents, store, project="BILLING")
    assert ctx.status == "ok"


async def test_agents_unknown_project_steers_to_scaffolding(store: StandardsStore):
    body, ctx = await run(agents, store, project="ghost")
    assert ctx.status == "error"
    assert "billing" in body
    assert "playbook_scaffold_standards" in body


# ---------------------------------------------------------------------------
# playbook_get_guardrails
# ---------------------------------------------------------------------------


async def test_guardrails_returns_guardrails_and_git(store: StandardsStore):
    body, ctx = await run(guardrails, store, project="billing")
    assert ctx.status == "ok"
    assert "guardrails.md" in body
    assert "git.md" in body
    assert "core/" not in body


# ---------------------------------------------------------------------------
# playbook_get_standards (language required)
# ---------------------------------------------------------------------------


async def test_standards_requires_language(store: StandardsStore):
    body, ctx = await run(standards, store, project="billing")
    assert ctx.status == "error"
    assert "needs a `language`" in body
    assert "java" in body


async def test_standards_returns_language_docs(store: StandardsStore):
    body, ctx = await run(standards, store, project="billing", language="java")
    assert ctx.status == "ok"
    assert "languages/java/standards.md" in body
    assert "languages/java/testing.md" in body


async def test_standards_unknown_language_lists_available(store: StandardsStore):
    body, ctx = await run(standards, store, project="billing", language="cobol")
    assert ctx.status == "error"
    assert "java" in body


# ---------------------------------------------------------------------------
# playbook_get_patterns (no language)
# ---------------------------------------------------------------------------


async def test_patterns_lists_all(store: StandardsStore):
    body, ctx = await run(patterns, store, project="billing")
    assert ctx.status == "ok"
    assert "patterns" in body.lower()
    assert "repository" in body


async def test_patterns_reads_one_by_name(store: StandardsStore):
    body, ctx = await run(patterns, store, project="billing", name="repository")
    assert ctx.status == "ok"
    assert ctx.doc_path is not None and ctx.doc_path.startswith("patterns/")
    assert "repository" in body.lower()


async def test_patterns_unknown_name_lists_available(store: StandardsStore):
    body, ctx = await run(patterns, store, project="billing", name="nonsense")
    assert ctx.status == "error"
    assert "repository" in body


# ---------------------------------------------------------------------------
# playbook_get_workflow (intent | name | list)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("fix bug in the retry path", "workflows/bug-fix.md"),
        ("the service is broken on startup", "workflows/bug-fix.md"),
        ("add a new feature for exports", "workflows/new-feature.md"),
        ("upgrade a dependency", "workflows/dependency-upgrade.md"),
    ],
)
async def test_workflow_matches_from_triggers(store: StandardsStore, intent: str, expected: str):
    _body, ctx = await run(workflow, store, project="billing", intent=intent)
    assert ctx.doc_path == expected


async def test_workflow_by_name(store: StandardsStore):
    body, ctx = await run(workflow, store, project="billing", name="bug-fix")
    assert ctx.doc_path == "workflows/bug-fix.md"
    assert "bug" in body.lower()


async def test_workflow_list_without_args(store: StandardsStore):
    body, ctx = await run(workflow, store, project="billing")
    assert ctx.status == "ok"
    assert "bug-fix" in body


async def test_workflow_no_match_lists_available(store: StandardsStore):
    body, ctx = await run(workflow, store, project="billing", intent="xyzzy")
    assert ctx.status == "error"
    assert "bug-fix" in body


# ---------------------------------------------------------------------------
# playbook_get_gates
# ---------------------------------------------------------------------------


async def test_gates_returns_dod_and_verify_scripts(store: StandardsStore):
    body, ctx = await run(gates, store, project="billing")
    assert ctx.status == "ok"
    assert "gates/definition-of-done.md" in body
    assert "verify-java.sh" in body


async def test_gates_language_narrows_verify_script(store: StandardsStore):
    body, ctx = await run(gates, store, project="billing", language="java")
    assert ctx.status == "ok"
    assert "verify-java.sh" in body


# ---------------------------------------------------------------------------
# playbook_find_standards
# ---------------------------------------------------------------------------


async def test_find_without_query_lists_everything(store: StandardsStore):
    body, ctx = await run(find, store, project="billing", top_k=50)
    assert ctx.status == "ok"
    assert "documents" in body
    assert "guardrails" in body


async def test_find_truncation_says_how_to_see_more(store: StandardsStore):
    body, _ctx = await run(find, store, project="billing", top_k=2)
    assert "more not shown" in body
    assert "top_k" in body


async def test_find_ranks_the_obvious_document_first(store: StandardsStore):
    _body, ctx = await run(find, store, project="billing", query="definition of done")
    assert ctx.top_result_path == "gates/definition-of-done.md"
    assert ctx.top_result_score and ctx.top_result_score > 0


async def test_find_records_the_query_for_telemetry(store: StandardsStore):
    _body, ctx = await run(find, store, project="billing", query="commit message")
    assert ctx.query == "commit message"


async def test_find_type_filter_restricts_to_a_family(store: StandardsStore):
    body, ctx = await run(find, store, project="billing", type="workflow", top_k=50)
    assert ctx.status == "ok"
    assert "playbook_get_workflow" in body
    assert "guardrails.md" not in body


async def test_find_no_match_suggests_widening(store: StandardsStore):
    body, ctx = await run(find, store, project="billing", query="zzzzqqqx")
    assert ctx.status == "error"
    assert "no query" in body


async def test_find_top_k_is_clamped(store: StandardsStore):
    body, _ctx = await run(find, store, project="billing", top_k=9999)
    assert "more not shown" not in body


# ---------------------------------------------------------------------------
# Next Calls route to real, working tool calls
# ---------------------------------------------------------------------------

_MODULES = {m.NAME: m for m in (agents, guardrails, standards, patterns, workflow, gates)}
_CALL_RE = re.compile(r"(playbook_get_\w+)\(([^)]*)\)")


def _parse_args(arg_text: str) -> dict:
    return {k: v for k, v in re.findall(r'(\w+)="([^"]*)"', arg_text)}


async def test_find_list_next_calls_resolve(store: StandardsStore):
    """Every call the list prints has to dispatch to a real tool without error."""
    body, _ = await run(find, store, project="billing", top_k=50)
    calls = _CALL_RE.findall(body)
    assert calls
    seen = 0
    for tool, arg_text in calls:
        module = _MODULES.get(tool)
        if module is None:
            continue
        args = _parse_args(arg_text)
        ctx = Ctx()
        result = await module.dispatch(module.NAME, args, ctx, store)
        assert result is not None
        assert ctx.status == "ok", (tool, args, result[0].text[:120])
        seen += 1
    assert seen
