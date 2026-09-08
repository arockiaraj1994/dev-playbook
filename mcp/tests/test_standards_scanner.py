"""
Unit tests for the standards scanner (store-backed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from standards_scanner import (
    _extract_description,
    _extract_title,
    _parse_frontmatter,
    _run_file_rules,
    scan_all,
    scan_project,
)
from standards_store import StandardsStore


def test_parse_frontmatter():
    content = "---\ntitle: Hello\ndescription: World\n---\nBody here"
    meta, body = _parse_frontmatter(content)
    assert meta == {"title": "Hello", "description": "World"}
    assert body == "Body here"

    # Missing frontmatter
    meta, body = _parse_frontmatter("Just body")
    assert meta == {}
    assert body == "Just body"


def test_extract_title():
    assert _extract_title({"title": "From Meta"}, "Body", "file.md") == "From Meta"
    assert _extract_title({}, "# From H1\nContent", "file.md") == "From H1"
    assert _extract_title({}, "Just body", "file.md") == "file.md"


def test_extract_description():
    assert _extract_description({"description": "Hello"}) == "Hello"
    assert _extract_description({}) == ""


def test_run_file_rules_gate_executable_flag():
    passing = _run_file_rules(
        "gates/scripts/check.sh", {"title": "T", "description": "D"}, "A" * 100, True
    )
    failing = _run_file_rules(
        "gates/scripts/check.sh", {"title": "T", "description": "D"}, "A" * 100, False
    )
    assert next(r for r in passing if r.rule_id == "gate-executable").passed
    assert not next(r for r in failing if r.rule_id == "gate-executable").passed


@pytest.fixture
async def store(tmp_path: Path) -> StandardsStore:
    s = StandardsStore(tmp_path / "standards.db")
    await s.init()
    return s


_VALID_BODY = (
    "This is a long enough body to pass the minimum content length check.\n"
    "It must be at least eighty characters long to be considered valid.\n"
    "```python\nprint('code block')\n```\n"
)

_REQUIRED_FILES = (
    "AGENTS.md",
    "core/guardrails.md",
    "core/definition-of-done.md",
    "core/glossary.md",
    "gates/README.md",
    "workflows/new-feature.md",
    "workflows/bug-fix.md",
    "workflows/security-fix.md",
    "workflows/refactor.md",
)


async def _seed_valid_project(store: StandardsStore, project: str) -> None:
    for path in _REQUIRED_FILES:
        await store.upsert_file(
            project=project,
            relative_path=path,
            kind="markdown",
            title="Test Doc",
            description="Valid description for the file.",
            frontmatter="title: Test Doc\ndescription: Valid description for the file.",
            body=_VALID_BODY,
            expected_version=None,
        )


async def test_scan_project_missing_required(store: StandardsStore):
    status = await scan_project(store, "empty")
    assert status.project == "empty"
    assert status.indicator == "red"
    assert "AGENTS.md" in status.missing_required
    assert "workflows/new-feature.md" in status.missing_required


async def test_scan_project_healthy(store: StandardsStore):
    await _seed_valid_project(store, "healthy")
    status = await scan_project(store, "healthy")
    assert status.indicator == "green"
    assert not status.missing_required
    assert status.counts["red"] == 0
    assert status.counts["amber"] == 0
    assert status.counts["green"] == len(_REQUIRED_FILES)

    agents = next(f for f in status.files if f.relative_path == "AGENTS.md")
    assert agents.raw_body == _VALID_BODY
    assert agents.frontmatter == {
        "title": "Test Doc",
        "description": "Valid description for the file.",
    }


async def test_scan_project_with_gate_script(store: StandardsStore):
    await _seed_valid_project(store, "gates_test")
    await store.upsert_file(
        project="gates_test",
        relative_path="gates/scripts/check.sh",
        kind="script",
        title="check.sh",
        body="#!/bin/bash",
        is_executable=False,
        expected_version=None,
    )

    status = await scan_project(store, "gates_test")
    assert status.indicator == "amber"
    assert status.counts["amber"] == 1
    script_status = next(f for f in status.files if f.relative_path == "gates/scripts/check.sh")
    assert script_status.raw_body == "#!/bin/bash"
    assert script_status.frontmatter == {}

    row = await store.get_file("gates_test", "gates/scripts/check.sh")
    await store.upsert_file(
        project="gates_test",
        relative_path="gates/scripts/check.sh",
        kind="script",
        title="check.sh",
        body="#!/bin/bash",
        is_executable=True,
        expected_version=row.version,
    )
    status2 = await scan_project(store, "gates_test")
    assert status2.indicator == "green"


async def test_scan_all(store: StandardsStore):
    await _seed_valid_project(store, "proj1")
    await _seed_valid_project(store, "proj2")

    projects = await scan_all(store)
    assert {p.project for p in projects} == {"proj1", "proj2"}
