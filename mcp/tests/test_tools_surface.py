"""Tests for the surface itself: what list_tools advertises and how it routes."""

from __future__ import annotations

import pytest

import server
from tools.common import POLICY


@pytest.fixture(autouse=True)
def open_policy():
    POLICY.scaffold_enabled = True
    POLICY.auth_enabled = False
    yield
    POLICY.scaffold_enabled = True
    POLICY.auth_enabled = False


EXPECTED = {
    "playbook_get_agents",
    "playbook_get_guardrails",
    "playbook_get_standards",
    "playbook_get_patterns",
    "playbook_get_workflow",
    "playbook_get_gates",
    "playbook_find_standards",
    "playbook_list_templates",
    "playbook_scaffold_standards",
}


async def test_all_tools_are_advertised():
    names = {t.name for t in await server.list_tools()}
    assert names == EXPECTED


async def test_the_read_tools_come_first():
    tools = await server.list_tools()
    assert tools[0].name == "playbook_get_agents"
    assert tools[-1].name == "playbook_scaffold_standards"


async def test_every_tool_is_annotated():
    """A client that sees no annotations is entitled to assume the worst."""
    for tool in await server.list_tools():
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is not None
        assert tool.annotations.destructiveHint is not None
        assert tool.annotations.idempotentHint is not None


async def test_only_scaffolding_is_a_write():
    writes = {t.name for t in await server.list_tools() if t.annotations.readOnlyHint is False}
    assert writes == {"playbook_scaffold_standards"}


async def test_scaffolding_is_additive_but_not_idempotent():
    """Additive so a client need not warn about data loss; non-idempotent so it
    still asks before calling twice."""
    tool = next(t for t in await server.list_tools() if t.name == "playbook_scaffold_standards")
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is False


async def test_every_tool_has_a_description_with_an_example():
    for tool in await server.list_tools():
        assert tool.description
        assert "Example:" in tool.description or "example" in tool.description.lower()


async def test_descriptions_do_not_reuse_ai_or_agent_persona_words():
    """The surface was rewritten to name concrete documents, not AI framing."""
    banned = ("ai agent", "you are a senior", "before writing or changing")
    for tool in await server.list_tools():
        lowered = tool.description.lower()
        for phrase in banned:
            assert phrase not in lowered, (tool.name, phrase)


async def test_required_params_are_declared():
    required = {t.name: set(t.inputSchema.get("required", [])) for t in await server.list_tools()}
    assert required["playbook_get_agents"] == {"project"}
    assert required["playbook_get_guardrails"] == {"project"}
    assert required["playbook_get_standards"] == {"project", "language"}
    assert required["playbook_get_patterns"] == {"project"}
    assert required["playbook_get_workflow"] == {"project"}
    assert required["playbook_get_gates"] == {"project"}
    assert required["playbook_find_standards"] == {"project"}
    assert required["playbook_scaffold_standards"] == {"project", "languages"}
    assert required["playbook_list_templates"] == set()


async def test_no_tool_takes_more_than_eight_parameters():
    for tool in await server.list_tools():
        assert len(tool.inputSchema.get("properties", {})) <= 8


async def test_disabling_scaffolding_hides_it():
    POLICY.scaffold_enabled = False
    names = {t.name for t in await server.list_tools()}
    assert "playbook_scaffold_standards" not in names
    assert len(names) == 8


async def test_unknown_tool_names_the_real_ones(monkeypatch):
    monkeypatch.setattr(server, "standards_store", object())
    ctx = server._CallContext()
    result = await server._dispatch_typed("playbook_nope", {}, ctx)
    assert ctx.status == "error"
    assert "playbook_get_agents" in result[0].text


async def test_dispatch_without_a_store_fails_cleanly(monkeypatch):
    monkeypatch.setattr(server, "standards_store", None)
    ctx = server._CallContext()
    result = await server._dispatch_typed("playbook_get_agents", {}, ctx)
    assert ctx.status == "error"
    assert "not ready" in result[0].text
