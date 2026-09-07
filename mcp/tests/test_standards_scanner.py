"""
Unit tests for the standards scanner.
"""

from __future__ import annotations

import stat
from pathlib import Path

from standards_scanner import _parse_frontmatter, _extract_title, _extract_description, _run_file_rules, scan_project, scan_all


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
    # From meta
    assert _extract_title({"title": "From Meta"}, "Body", "file.md") == "From Meta"
    # From H1
    assert _extract_title({}, "# From H1\nContent", "file.md") == "From H1"
    # Fallback to filename
    assert _extract_title({}, "Just body", "file.md") == "file.md"


def test_extract_description():
    assert _extract_description({"description": "Hello"}) == "Hello"
    assert _extract_description({}) == ""


def test_scan_project_missing_required(tmp_path: Path):
    # Empty project
    status = scan_project("empty", tmp_path)
    assert status.project == "empty"
    assert status.indicator == "red"
    assert "AGENTS.md" in status.missing_required
    assert "workflows/new-feature.md" in status.missing_required


def test_scan_project_healthy(tmp_path: Path):
    # Setup healthy project structure
    (tmp_path / "core").mkdir()
    (tmp_path / "workflows").mkdir()
    (tmp_path / "gates" / "scripts").mkdir(parents=True)

    # Required files
    files = [
        "AGENTS.md",
        "core/guardrails.md",
        "core/definition-of-done.md",
        "core/glossary.md",
        "gates/README.md",
        "workflows/new-feature.md",
        "workflows/bug-fix.md",
        "workflows/security-fix.md",
        "workflows/refactor.md",
    ]

    valid_content = (
        "---\n"
        "title: Test Doc\n"
        "description: Valid description for the file.\n"
        "---\n\n"
        "This is a long enough body to pass the minimum content length check.\n"
        "It must be at least eighty characters long to be considered valid.\n"
        "```python\nprint('code block')\n```\n"
    )

    for f in files:
        p = tmp_path / f
        p.write_text(valid_content)

    status = scan_project("healthy", tmp_path)
    assert status.indicator == "green"
    assert not status.missing_required
    assert status.counts["red"] == 0
    assert status.counts["amber"] == 0
    assert status.counts["green"] == len(files)


def test_scan_project_with_gate_script(tmp_path: Path):
    # Setup project with an un-executable gate script
    (tmp_path / "gates" / "scripts").mkdir(parents=True)
    
    # Required files to avoid red indicator from missing files
    files = [
        "AGENTS.md",
        "core/guardrails.md",
        "core/definition-of-done.md",
        "core/glossary.md",
        "gates/README.md",
        "workflows/new-feature.md",
        "workflows/bug-fix.md",
        "workflows/security-fix.md",
        "workflows/refactor.md",
    ]
    for f in files:
        (tmp_path / f).parent.mkdir(exist_ok=True, parents=True)
        (tmp_path / f).write_text("---\ntitle: T\ndescription: D\n---\n" + "A" * 100 + "\n```\n```\n")

    script = tmp_path / "gates" / "scripts" / "check.sh"
    script.write_text("#!/bin/bash")
    
    # Should be amber because script is not executable
    status = scan_project("gates_test", tmp_path)
    assert status.indicator == "amber"
    assert status.counts["amber"] == 1
    
    # Make executable
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    status2 = scan_project("gates_test", tmp_path)
    assert status2.indicator == "green"


def test_scan_all(tmp_path: Path):
    (tmp_path / "proj1").mkdir()
    (tmp_path / "proj2").mkdir()
    (tmp_path / ".hidden").mkdir()
    
    projects = scan_all(tmp_path)
    assert len(projects) == 2
    assert {p.project for p in projects} == {"proj1", "proj2"}
