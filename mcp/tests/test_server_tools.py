"""
Tests for the MCP tool handlers in server.py.

server.py loads corpora at import time. We point MCP_STANDARDS_ROOT (and an
empty MCP_REQUIREMENTS_ROOT) at a temp tree *before* importing server, and
reload corpus/loader/cache/server so the bootstrap picks up the fixture.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path

import pytest


def _import_server_with_root(tmp_rules_root: Path):
    """(Re)import the server module with standards pointing at tmp_rules_root."""
    empty_req = tmp_rules_root / "_empty_requirements"
    empty_req.mkdir(exist_ok=True)
    os.environ["MCP_STANDARDS_ROOT"] = str(tmp_rules_root)
    os.environ["MCP_REQUIREMENTS_ROOT"] = str(empty_req)

    # Drop cached modules so corpus specs and bootstrap re-read env.
    for mod in (
        "server",
        "cache",
        "corpus",
        "loader",
        "search",
        "refs",
        "tools.common",
        "tools.get",
        "tools.find",
        "tools.start",
    ):
        sys.modules.pop(mod, None)

    return importlib.import_module("server")


@pytest.fixture
def srv(tmp_rules_root: Path):
    return _import_server_with_root(tmp_rules_root)


async def _call(srv, tool_name: str, **arguments):
    return await srv.dispatch_tool(tool_name, arguments)


# ---------------------------------------------------------------------------
# playbook_find
# ---------------------------------------------------------------------------


async def test_find_list_mode(srv) -> None:
    result = await _call(srv, "playbook_find", project="proj-a")
    text = result[0].text
    assert "AGENTS.md" in text
    assert "patterns/foo.md" in text
    assert "skills/bar.md" in text
    assert "workflows/bug-fix.md" in text
    assert "core/guardrails.md" in text


async def test_find_list_mode_surfaces_triggers(srv) -> None:
    # This is what makes dropping get_index lossless.
    result = await _call(srv, "playbook_find", project="proj-a")
    text = result[0].text
    assert "Triggers:" in text
    assert "fix a bug" in text


async def test_find_filtered_by_type(srv) -> None:
    result = await _call(srv, "playbook_find", project="proj-a", type="pattern")
    text = result[0].text
    assert "patterns/foo.md" in text
    assert "skills/bar.md" not in text


async def test_find_unknown_project_lists_available(srv) -> None:
    result = await _call(srv, "playbook_find", project="nope")
    text = result[0].text
    assert "not found" in text.lower()
    # The error itself teaches the valid projects (list_projects is gone).
    assert "proj-a" in text
    assert "proj-b" in text


async def test_find_search_mode(srv) -> None:
    result = await _call(srv, "playbook_find", project="proj-a", query="DLQ flows")
    assert "patterns/foo.md" in result[0].text


async def test_find_blank_query_falls_back_to_list(srv) -> None:
    """A whitespace-only query is a listing request, not an error."""
    result = await _call(srv, "playbook_find", project="proj-a", query="   ")
    assert "patterns/foo.md" in result[0].text


async def test_find_top_k_bounds_clamp(srv) -> None:
    """Bad top_k values are clamped, not crashed on."""
    r1 = await _call(srv, "playbook_find", project="proj-a", query="agents", top_k=-5)
    r2 = await _call(srv, "playbook_find", project="proj-a", query="agents", top_k=9999)
    assert r1[0].text  # both should produce non-empty results, no exception
    assert r2[0].text


# ---------------------------------------------------------------------------
# playbook_get - one ref per doc kind
# ---------------------------------------------------------------------------


async def test_get_agents(srv) -> None:
    result = await _call(srv, "playbook_get", ref="agents", project="proj-a")
    assert "AGENTS.md - Proj A" in result[0].text


async def test_get_agents_chains_to_start(srv) -> None:
    """The dead end that made get_agents_md the most-called tool: it used to
    return no Next Calls at all, so the agent had nowhere to go."""
    result = await _call(srv, "playbook_get", ref="agents", project="proj-a")
    text = result[0].text
    assert "## Next Calls" in text
    assert 'playbook_start(project="proj-a"' in text
    assert "START HERE" in text


async def test_get_agents_unknown_project(srv) -> None:
    result = await _call(srv, "playbook_get", ref="agents", project="nope")
    assert "not found" in result[0].text.lower()


async def test_get_guardrails(srv) -> None:
    result = await _call(srv, "playbook_get", ref="guardrails", project="proj-a")
    text = result[0].text
    assert "Guardrails" in text
    assert "MUST do X" in text
    assert "Definition of Done" in text


async def test_get_architecture_overview(srv) -> None:
    result = await _call(srv, "playbook_get", ref="architecture", project="proj-a")
    assert "Architecture - Proj A" in result[0].text


async def test_get_architecture_adr(srv) -> None:
    result = await _call(srv, "playbook_get", ref="architecture:0001-pick-foo", project="proj-a")
    assert "ADR 0001" in result[0].text


async def test_get_language_defaults_to_standards(srv) -> None:
    result = await _call(srv, "playbook_get", ref="language:java", project="proj-a")
    assert "Java standards" in result[0].text


async def test_get_language_testing_section(srv) -> None:
    """The former `section=` argument folds into the ref."""
    result = await _call(srv, "playbook_get", ref="language:java/testing", project="proj-a")
    assert "JUnit 5" in result[0].text


async def test_get_language_invalid_section(srv) -> None:
    result = await _call(srv, "playbook_get", ref="language:java/bogus", project="proj-a")
    assert "expected one of" in result[0].text.lower()


async def test_get_pattern(srv) -> None:
    result = await _call(srv, "playbook_get", ref="pattern:foo", project="proj-a")
    assert "Pattern: Foo" in result[0].text


async def test_get_pattern_missing(srv) -> None:
    result = await _call(srv, "playbook_get", ref="pattern:zzz", project="proj-a")
    assert "not found" in result[0].text.lower()
    assert "playbook_find" in result[0].text


async def test_get_skill(srv) -> None:
    result = await _call(srv, "playbook_get", ref="skill:bar", project="proj-a")
    assert "Skill: Bar" in result[0].text


async def test_get_skill_missing(srv) -> None:
    result = await _call(srv, "playbook_get", ref="skill:zzz", project="proj-a")
    assert "not found" in result[0].text.lower()


async def test_get_workflow(srv) -> None:
    result = await _call(srv, "playbook_get", ref="workflow:bug-fix", project="proj-a")
    text = result[0].text
    assert "Reproduce" in text
    # see_also drives Next Calls, rendered as playbook_get refs
    assert "Next Calls" in text
    assert 'ref="skill:bar"' in text
    assert 'ref="pattern:foo"' in text


async def test_see_also_core_kind_renders(srv) -> None:
    """`core:guardrails` used to be silently dropped by _format_call, so three
    real nexre workflows shipped with a Next Call that rendered nothing."""
    result = await _call(srv, "playbook_get", ref="workflow:bug-fix", project="proj-a")
    assert 'playbook_get(project="proj-a", ref="guardrails")' in result[0].text


async def test_see_also_gates_alias_renders(srv) -> None:
    """Same bug, plural spelling: `gates:README` rendered nothing."""
    result = await _call(srv, "playbook_get", ref="skill:bar", project="proj-a")
    assert 'playbook_get(project="proj-a", ref="gate")' in result[0].text


async def test_get_gate_listing(srv) -> None:
    result = await _call(srv, "playbook_get", ref="gate", project="proj-a")
    text = result[0].text
    assert "verify-java.sh" in text
    assert "Available scripts" in text


async def test_get_gate_named(srv) -> None:
    result = await _call(srv, "playbook_get", ref="gate:verify-java", project="proj-a")
    text = result[0].text
    assert "verify-java.sh" in text
    assert "does not execute" in text


async def test_get_gate_named_shows_script_body(srv) -> None:
    """The description promises the script's first lines; it used to show only
    the path."""
    result = await _call(srv, "playbook_get", ref="gate:verify-java", project="proj-a")
    assert "echo ok" in result[0].text


async def test_get_gate_unknown_script(srv) -> None:
    result = await _call(srv, "playbook_get", ref="gate:verify-zzz", project="proj-a")
    assert "not found" in result[0].text.lower()


async def test_get_missing_project_asks_which(srv) -> None:
    result = await _call(srv, "playbook_get", ref="guardrails")
    text = result[0].text.lower()
    assert "which project" in text


async def test_get_requires_ref(srv) -> None:
    result = await _call(srv, "playbook_get", project="proj-a")
    assert "`ref` is required" in result[0].text


async def test_get_unknown_ref_kind_teaches_the_grammar(srv) -> None:
    result = await _call(srv, "playbook_get", project="proj-a", ref="bogus:x")
    text = result[0].text
    assert "unknown ref kind" in text.lower()
    assert "pattern" in text  # the error lists the valid kinds


async def test_get_ref_needing_a_name(srv) -> None:
    result = await _call(srv, "playbook_get", project="proj-a", ref="pattern")
    assert "needs a name" in result[0].text


# ---------------------------------------------------------------------------
# playbook_start
# ---------------------------------------------------------------------------


async def test_start_matches_workflow_via_trigger(srv) -> None:
    result = await _call(
        srv, "playbook_start", project="proj-a", intent="please fix a bug in the route"
    )
    text = result[0].text
    assert "Guardrails" in text
    assert "Definition of Done" in text
    assert "bug-fix" in text
    assert "Next Calls" in text
    assert 'ref="skill:bar"' in text


async def test_start_inlines_identity(srv) -> None:
    """playbook_start subsumes the one part of AGENTS.md it did not already
    cover, so there is no reason left to call playbook_get(ref="agents") first."""
    result = await _call(srv, "playbook_start", project="proj-a", intent="fix a bug")
    text = result[0].text
    assert "senior proj-a engineer" in text
    # ...but not the rest of AGENTS.md, which just restates the guardrails.
    assert "errorHandler boundaries" not in text


async def test_start_guardrails_block_is_identical_to_get(srv) -> None:
    """The v0.8.0 contract: playbook_start embeds get.render_ref() output rather
    than re-rendering it. Before 0.8.0 these two blocks were separate code paths
    that drifted. If this fails, the duplication is back."""
    started = (await _call(srv, "playbook_start", project="proj-a", intent="fix a bug"))[0].text
    fetched = (await _call(srv, "playbook_get", project="proj-a", ref="guardrails"))[0].text
    block = started.split("## Always-on rules\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert block.strip() == fetched.strip()


async def test_start_unknown_project(srv) -> None:
    result = await _call(srv, "playbook_start", project="nope", intent="something")
    assert "not found" in result[0].text.lower()


async def test_start_missing_project_asks_which(srv) -> None:
    """`project` is schema-required; a non-enforcing client that omits it must
    still get the teaching error listing the valid projects."""
    result = await _call(srv, "playbook_start", intent="fix a bug")
    text = result[0].text.lower()
    assert "which project" in text
    assert "proj-a" in text
    assert "proj-b" in text


async def test_start_requires_intent(srv) -> None:
    result = await _call(srv, "playbook_start", project="proj-a")
    assert "`intent` is required" in result[0].text


async def test_start_rejects_a_non_requirement_ref(srv) -> None:
    """`ref` on playbook_start names a requirement, not an arbitrary doc."""
    result = await _call(srv, "playbook_start", project="proj-a", intent="x", ref="pattern:foo")
    assert "names a requirement" in result[0].text


async def test_start_story_mode_needs_parent_prd(srv) -> None:
    result = await _call(srv, "playbook_start", project="proj-a", intent="x", mode="story")
    assert "required" in result[0].text.lower()


async def test_start_rejects_unknown_mode(srv) -> None:
    result = await _call(srv, "playbook_start", project="proj-a", intent="x", mode="bogus")
    assert "`mode` must be one of" in result[0].text


# ---------------------------------------------------------------------------
# Removed tools
# ---------------------------------------------------------------------------


async def test_v070_tool_names_removed(srv) -> None:
    """Breaking change in 0.8.0: the five-tool surface is gone."""
    for old in (
        "playbook_start_task",
        "playbook_get_doc",
        "playbook_search_docs",
        "playbook_list_requirements",
        "playbook_start_requirement",
    ):
        result = await _call(srv, old, project="proj-a", kind="agents", task="x", intent="x")
        assert "Unknown tool" in result[0].text, old


async def test_pre_070_unprefixed_names_removed(srv) -> None:
    for old in ("start_task", "get_doc", "find_rules", "list_requirements", "start_requirement"):
        result = await _call(srv, old, project="proj-a", task="x", kind="agents", intent="x")
        assert "Unknown tool" in result[0].text, old


async def test_unknown_tool(srv) -> None:
    result = await _call(srv, "definitely_not_a_tool")
    assert "Unknown tool" in result[0].text


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


async def test_tool_surface_is_read_only(srv) -> None:
    tools = await srv.list_tools()
    assert len(tools) == 3
    names = {t.name for t in tools}
    assert names == {"playbook_start", "playbook_get", "playbook_find"}
    for t in tools:
        assert t.annotations is not None, f"{t.name} has no annotations"
        assert t.annotations.readOnlyHint is True, t.name
        assert t.annotations.openWorldHint is False, t.name


async def test_every_tool_requires_project(srv) -> None:
    """`project` is required on every tool - no inference surprises."""
    tools = await srv.list_tools()
    for t in tools:
        assert "project" in (t.inputSchema.get("required") or []), t.name


async def test_exactly_one_tool_claims_to_be_first(srv) -> None:
    """playbook_start is the entry point and must say so alone, or agents get
    confused about where to begin."""
    directive = re.compile(r"entry point", re.IGNORECASE)
    tools = await srv.list_tools()
    claimants = [t.name for t in tools if directive.search(t.description or "")]
    assert claimants == ["playbook_start"]


async def test_server_instructions_name_the_entry_point(srv) -> None:
    """The cross-tool workflow lives in server-level instructions, not in
    ALL-CAPS tool descriptions."""
    opts = srv._initialization_options()
    assert opts.instructions
    assert "playbook_start" in opts.instructions
