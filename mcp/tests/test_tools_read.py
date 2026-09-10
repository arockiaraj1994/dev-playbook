"""Tests for playbook_get_standard, playbook_find_standards, playbook_start_task."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

import scaffold_service
from standards_store import StandardsStore
from tools import find, get, start

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
# playbook_get_standard
# ---------------------------------------------------------------------------


async def test_get_by_alias(store: StandardsStore):
    body, ctx = await run(get, store, project="billing", ref="guardrails")
    assert ctx.status == "ok"
    assert ctx.doc_path == "core/guardrails.md"
    assert "core/guardrails.md" in body


async def test_get_by_exact_path(store: StandardsStore):
    by_alias, _ = await run(get, store, project="billing", ref="guardrails")
    by_path, _ = await run(get, store, project="billing", ref="core/guardrails.md")
    assert by_alias == by_path


async def test_get_workflow_by_prefix(store: StandardsStore):
    body, ctx = await run(get, store, project="billing", ref="workflow:bug-fix")
    assert ctx.doc_path == "workflows/bug-fix.md"
    assert "bug" in body.lower()


async def test_get_script_is_fenced_as_shell(store: StandardsStore):
    rows = await store.list_files("billing")
    script = next(r for r in rows if r.kind == "script")
    body, ctx = await run(get, store, project="billing", ref=script.relative_path)
    assert ctx.status == "ok"
    assert "```sh" in body


async def test_project_name_is_matched_case_insensitively(store: StandardsStore):
    _body, ctx = await run(get, store, project="BILLING", ref="guardrails")
    assert ctx.status == "ok"


async def test_unknown_project_names_what_exists(store: StandardsStore):
    body, ctx = await run(get, store, project="nope", ref="guardrails")
    assert ctx.status == "error"
    assert "billing" in body
    assert "playbook_scaffold_standards" in body


async def test_empty_store_tells_you_to_scaffold(tmp_path: Path):
    empty = StandardsStore(tmp_path / "empty.db")
    await empty.init()
    body, ctx = await run(get, empty, project="anything", ref="guardrails")
    assert ctx.status == "error"
    assert "no standards projects at all" in body
    assert "playbook_list_templates" in body


async def test_unresolvable_ref_lists_what_the_project_has(store: StandardsStore):
    body, ctx = await run(get, store, project="billing", ref="nonsense")
    assert ctx.status == "error"
    assert "guardrails" in body
    assert "playbook_find_standards" in body


async def test_every_listed_ref_actually_resolves(store: StandardsStore):
    """The miss text advertises refs; each one has to work, or it is a dead end."""
    from tools.refs import format_ref

    for row in await store.list_files("billing"):
        assert await get.resolve(store, "billing", format_ref(row.relative_path)) is not None


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
    assert ctx.top_result_path == "core/definition-of-done.md"
    assert ctx.top_result_score and ctx.top_result_score > 0


async def test_find_records_the_query_for_telemetry(store: StandardsStore):
    _body, ctx = await run(find, store, project="billing", query="commit message")
    assert ctx.query == "commit message"


async def test_find_type_filter_restricts_to_a_directory(store: StandardsStore):
    body, ctx = await run(find, store, project="billing", type="workflow", top_k=50)
    assert ctx.status == "ok"
    assert "workflow:bug-fix" in body
    assert "guardrails" not in body


async def test_find_no_match_suggests_widening(store: StandardsStore):
    body, ctx = await run(find, store, project="billing", query="zzzzqqqx")
    assert ctx.status == "error"
    assert "no query" in body


async def test_find_top_k_is_clamped(store: StandardsStore):
    body, _ctx = await run(find, store, project="billing", top_k=9999)
    assert "more not shown" not in body


# ---------------------------------------------------------------------------
# playbook_start_task
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
async def test_start_matches_the_workflow_from_its_triggers(
    store: StandardsStore, intent: str, expected: str
):
    _body, ctx = await run(start, store, project="billing", intent=intent)
    assert ctx.doc_path == expected


async def test_start_returns_guardrails_and_the_workflow(store: StandardsStore):
    body, ctx = await run(start, store, project="billing", intent="fix bug")
    assert ctx.status == "ok"
    assert "core/guardrails.md" in body
    assert "workflows/bug-fix.md" in body
    assert "Next Calls" in body


async def test_start_guardrails_are_byte_identical_to_get(store: StandardsStore):
    """The duplication issue #380 found had crept back twice. Composing
    get.render_ref is what stops it; this asserts the composition happened."""
    started, _ = await run(start, store, project="billing", intent="fix bug")
    fetched, _ = await run(get, store, project="billing", ref="guardrails")
    assert fetched in started


async def test_start_workflow_body_is_byte_identical_to_get(store: StandardsStore):
    started, _ = await run(start, store, project="billing", intent="fix bug")
    fetched, _ = await run(get, store, project="billing", ref="workflow:bug-fix")
    assert fetched in started


async def test_start_next_calls_resolve(store: StandardsStore):
    """A Next Call the model follows verbatim has to be a call that works."""
    import re

    body, _ = await run(start, store, project="billing", intent="fix bug")
    refs = re.findall(r'playbook_get_standard\(project="billing", ref="([^"]+)"\)', body)
    assert refs
    for ref in refs:
        assert await get.resolve(store, "billing", ref) is not None


async def test_start_without_a_match_says_so_and_lists_workflows(store: StandardsStore):
    body, ctx = await run(start, store, project="billing", intent="xyzzy")
    assert ctx.doc_path is None
    assert "No matching workflow" in body
    assert "workflow:bug-fix" in body


async def test_start_on_unknown_project_steers_to_scaffolding(store: StandardsStore):
    body, ctx = await run(start, store, project="ghost", intent="fix bug")
    assert ctx.status == "error"
    assert "playbook_scaffold_standards" in body
