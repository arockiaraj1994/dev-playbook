"""Tests for loader.py."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from loader import (
    _infer_doc_type,
    _infer_doc_type_and_name,
    _is_excluded,
    _parse_docs,
    _parse_frontmatter,
    load_store,
)

# -- _infer_doc_type_and_name (path-first dispatch) -------------------------


@pytest.mark.parametrize(
    "rel,expected_type,expected_name",
    [
        ("AGENTS.md", "agents", "agents"),
        ("INDEX.md", "index", "index"),
        ("core/guardrails.md", "guardrails", "guardrails"),
        ("core/definition-of-done.md", "definition-of-done", "definition-of-done"),
        ("core/glossary.md", "glossary", "glossary"),
        ("architecture/overview.md", "architecture", "overview"),
        ("architecture/decisions/0007-foo.md", "architecture-decision", "0007-foo"),
        ("languages/java/standards.md", "language-rules", "java/standards"),
        ("languages/typescript/testing.md", "language-rules", "typescript/testing"),
        ("languages/shell-yaml/anti-patterns.md", "language-rules", "shell-yaml/anti-patterns"),
        ("patterns/quarkus.md", "pattern", "quarkus"),
        ("skills/add-connector.md", "skill", "add-connector"),
        ("workflows/security-fix.md", "workflow", "security-fix"),
        ("gates/README.md", "gate", "gate"),
        # Anything outside the layout → other.
        ("agents.md", "other", "agents"),  # legacy lowercase no longer matches
        ("architecture.md", "other", "architecture"),
        ("misc/readme.md", "other", "readme"),
        ("languages/java.md", "other", "java"),
        ("core/random.md", "other", "random"),
        ("gates/extra.md", "other", "extra"),
    ],
)
def test_infer_doc_type_and_name(rel: str, expected_type: str, expected_name: str) -> None:
    doc_type, name = _infer_doc_type_and_name(rel)
    assert doc_type == expected_type
    assert name == expected_name


def test_infer_doc_type_alias() -> None:
    """`_infer_doc_type` is the type-only alias used by older callers."""
    assert _infer_doc_type("patterns/foo.md") == "pattern"


# -- _is_excluded -----------------------------------------------------------


@pytest.mark.parametrize(
    "rel,expected",
    [
        ("README.md", True),
        ("CONTRIBUTING.md", True),
        ("TEMPLATE.md", True),
        ("CHANGELOG.md", True),
        ("mcp/server.py", True),
        (".git/HEAD", True),
        (".github/workflows/ci.yml", True),
        ("scripts/validate-rules.py", True),
        # Per-project README.md is human-only - not indexed, not flagged.
        ("apache-camel/README.md", True),
        # Project rule docs are NOT excluded.
        ("nexre/AGENTS.md", False),
        ("apache-camel/patterns/foo.md", False),
        ("apache-camel/INDEX.md", False),
        ("standards/", True),
    ],
)
def test_is_excluded(rel: str, expected: bool) -> None:
    assert _is_excluded(rel) is expected


# -- _parse_frontmatter -----------------------------------------------------


def test_frontmatter_none() -> None:
    md, body = _parse_frontmatter("# Title\n\nBody")
    assert md == {}
    assert body == "# Title\n\nBody"


def test_frontmatter_basic() -> None:
    raw = (
        '---\ntitle: Hello\ndescription: A test doc\ntags: [a, b, "c d"]\n---\n# Title\nBody line\n'
    )
    md, body = _parse_frontmatter(raw)
    assert md["title"] == "Hello"
    assert md["description"] == "A test doc"
    assert md["tags"] == ["a", "b", "c d"]
    assert body == "# Title\nBody line\n"


def test_frontmatter_quoted_strings() -> None:
    raw = "---\ntitle: 'hello world'\nname: \"foo\"\n---\nbody"
    md, _ = _parse_frontmatter(raw)
    assert md["title"] == "hello world"
    assert md["name"] == "foo"


def test_frontmatter_unterminated() -> None:
    raw = "---\ntitle: Hello\nno close\n# body"
    md, body = _parse_frontmatter(raw)
    assert md == {}
    assert body == raw


def test_frontmatter_empty_value() -> None:
    raw = "---\ntitle:\n---\nbody"
    md, _ = _parse_frontmatter(raw)
    assert md["title"] == ""


def test_frontmatter_ignores_comment_lines() -> None:
    raw = "---\n# this is a comment\ntitle: Hello\n---\nbody"
    md, _ = _parse_frontmatter(raw)
    assert md == {"title": "Hello"}


# -- _parse_docs / load_store -----------------------------------------------


def test_parse_docs_loads_expected_files(tmp_rules_root: Path) -> None:
    docs = _parse_docs(tmp_rules_root)
    rels = {(d.project, d.relative_path) for d in docs}
    assert ("proj-a", "AGENTS.md") in rels
    assert ("proj-a", "INDEX.md") in rels
    assert ("proj-a", "core/guardrails.md") in rels
    assert ("proj-a", "core/definition-of-done.md") in rels
    assert ("proj-a", "core/glossary.md") in rels
    assert ("proj-a", "architecture/overview.md") in rels
    assert ("proj-a", "architecture/decisions/0001-pick-foo.md") in rels
    assert ("proj-a", "languages/java/standards.md") in rels
    assert ("proj-a", "patterns/foo.md") in rels
    assert ("proj-a", "skills/bar.md") in rels
    assert ("proj-a", "workflows/bug-fix.md") in rels
    assert ("proj-a", "gates/README.md") in rels
    # proj-b
    assert ("proj-b", "AGENTS.md") in rels
    assert ("proj-b", "languages/shell-yaml/standards.md") in rels
    # stray.md still indexed but as 'other'
    assert ("proj-b", "stray.md") in rels


def test_parse_docs_excludes_top_level_readme(tmp_rules_root: Path) -> None:
    docs = _parse_docs(tmp_rules_root)
    paths = [d.relative_path for d in docs]
    assert "README.md" not in paths


def test_parse_docs_excludes_per_project_readme(tmp_rules_root: Path) -> None:
    docs = _parse_docs(tmp_rules_root)
    for d in docs:
        assert d.relative_path != "README.md"


def test_parse_docs_warns_on_loose_top_level(
    tmp_rules_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        _parse_docs(tmp_rules_root)
    assert any("loose.md" in r.message for r in caplog.records)


def test_parse_docs_warns_on_other_doc_type(
    tmp_rules_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        _parse_docs(tmp_rules_root)
    assert any("stray.md" in r.message for r in caplog.records)


def test_parse_docs_strips_frontmatter_from_content(tmp_rules_root: Path) -> None:
    docs = _parse_docs(tmp_rules_root)
    proj_a_agents = next(
        d for d in docs if d.project == "proj-a" and d.relative_path == "AGENTS.md"
    )
    assert not proj_a_agents.content.startswith("---")
    assert proj_a_agents.metadata["title"] == "Proj A agents"
    assert proj_a_agents.metadata["tags"] == ["stack-a", "rest"]


def test_language_rules_carry_language_metadata(tmp_rules_root: Path) -> None:
    docs = _parse_docs(tmp_rules_root)
    java_std = next(
        d
        for d in docs
        if d.project == "proj-a" and d.relative_path == "languages/java/standards.md"
    )
    assert java_std.doc_type == "language-rules"
    assert java_std.name == "java/standards"
    assert java_std.metadata.get("language") == "java"


def test_gate_doc_lists_scripts_in_metadata(tmp_rules_root: Path) -> None:
    docs = _parse_docs(tmp_rules_root)
    gate = next(d for d in docs if d.project == "proj-a" and d.relative_path == "gates/README.md")
    assert gate.doc_type == "gate"
    assert "verify-java.sh" in gate.metadata.get("gate_scripts", [])


def test_workflow_frontmatter_indexes_triggers_and_see_also(tmp_rules_root: Path) -> None:
    docs = _parse_docs(tmp_rules_root)
    wf = next(
        d for d in docs if d.project == "proj-a" and d.relative_path == "workflows/bug-fix.md"
    )
    assert wf.doc_type == "workflow"
    assert wf.metadata.get("triggers") == ["bug", "fix a bug", "debug"]
    assert wf.metadata.get("see_also") == ["skill:bar", "pattern:foo", "core:guardrails"]
    assert wf.metadata.get("gates") == ["verify-java"]


def test_load_store_projects_and_lookups(tmp_rules_root: Path) -> None:
    store = load_store(tmp_rules_root)
    assert store.projects() == ["proj-a", "proj-b"]
    assert store.get("proj-a", "AGENTS.md") is not None
    assert store.get("proj-a", "missing.md") is None
    patterns = store.of_type("proj-a", "pattern")
    assert {d.name for d in patterns} == {"foo"}
    skills = store.of_type("proj-a", "skill")
    assert {d.name for d in skills} == {"bar"}
    workflows = store.of_type("proj-a", "workflow")
    assert {d.name for d in workflows} == {"bug-fix"}
    langs = store.of_type("proj-a", "language-rules")
    assert {d.name for d in langs} == {"java/standards", "java/testing"}
