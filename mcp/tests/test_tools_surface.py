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
    "playbook_start_task",
    "playbook_get_standard",
    "playbook_find_standards",
    "playbook_list_templates",
    "playbook_scaffold_standards",
}


async def test_the_five_tools_are_advertised():
    names = {t.name for t in await server.list_tools()}
    assert names == EXPECTED


async def test_the_entry_point_is_listed_first():
    tools = await server.list_tools()
    assert tools[0].name == "playbook_start_task"


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


async def test_required_params_are_declared():
    required = {t.name: set(t.inputSchema.get("required", [])) for t in await server.list_tools()}
    assert required["playbook_start_task"] == {"project", "intent"}
    assert required["playbook_get_standard"] == {"project", "ref"}
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
    assert len(names) == 4


async def test_unknown_tool_names_the_real_ones(monkeypatch):
    monkeypatch.setattr(server, "standards_store", object())
    ctx = server._CallContext()
    result = await server._dispatch_typed("playbook_nope", {}, ctx)
    assert ctx.status == "error"
    assert "playbook_start_task" in result[0].text


async def test_dispatch_without_a_store_fails_cleanly(monkeypatch):
    monkeypatch.setattr(server, "standards_store", None)
    ctx = server._CallContext()
    result = await server._dispatch_typed("playbook_start_task", {}, ctx)
    assert ctx.status == "error"
    assert "not ready" in result[0].text
