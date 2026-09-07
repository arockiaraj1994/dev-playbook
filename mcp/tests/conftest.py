"""Shared pytest fixtures."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

# Make `mcp/` importable as a flat package layout.
_MCP_DIR = Path(__file__).resolve().parent.parent
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def tmp_rules_root(tmp_path: Path) -> Path:
    """
    Build a small fake rules tree using the new path-first layout:

        tmp/
          proj-a/
            AGENTS.md (with frontmatter)
            INDEX.md
            README.md (excluded)
            core/{guardrails,definition-of-done,glossary}.md
            architecture/overview.md
            architecture/decisions/0001-pick-foo.md
            languages/java/standards.md
            languages/java/testing.md
            patterns/foo.md
            skills/bar.md
            workflows/bug-fix.md
            gates/README.md
            gates/scripts/verify-java.sh (executable)
          proj-b/
            AGENTS.md
            core/guardrails.md
            core/definition-of-done.md
            core/glossary.md
            architecture/overview.md
            languages/shell-yaml/standards.md
            stray.md (lands in 'other')
          README.md (excluded - top-level)
          loose.md (top-level, warned + skipped)
    """
    a = tmp_path / "proj-a"
    _write(
        a / "AGENTS.md",
        "---\n"
        "title: Proj A agents\n"
        "description: Identity and behavior for proj-a.\n"
        "tags: [stack-a, rest]\n"
        "see_also: [tool:playbook_start, tool:playbook_get, workflow:bug-fix]\n"
        "---\n"
        "# AGENTS.md - Proj A\n\n"
        "## IDENTITY\n\nYou are a senior proj-a engineer who fixes root causes.\n\n"
        "---\n\n"
        "## OTHER\n\nBe careful with errorHandler boundaries.\n",
    )
    _write(a / "INDEX.md", "<!-- AUTO-GENERATED -->\n# INDEX - proj-a\n")
    _write(a / "README.md", "# Proj A - humans only\n")
    _write(a / "core" / "guardrails.md", "# Guardrails - Proj A\nMUST do X.\n")
    _write(
        a / "core" / "definition-of-done.md",
        "# Definition of Done - Proj A\nTests + lint pass.\n",
    )
    _write(a / "core" / "glossary.md", "# Glossary - Proj A\n- DLQ: dead letter queue.\n")
    _write(
        a / "architecture" / "overview.md",
        "# Architecture - Proj A\n\nModules: api, service, model.\n",
    )
    _write(
        a / "architecture" / "decisions" / "0001-pick-foo.md",
        "# ADR 0001 - Pick Foo\nWe picked Foo because of bar.\n",
    )
    _write(
        a / "languages" / "java" / "standards.md",
        "---\nlanguage: java\n---\n# Java standards - Proj A\nPrefer records.\n",
    )
    _write(
        a / "languages" / "java" / "testing.md",
        "# Java testing - Proj A\nUse JUnit 5.\n",
    )
    _write(
        a / "patterns" / "foo.md",
        "---\nsee_also: [skill:bar]\n---\n# Pattern: Foo - Proj A\nUse Foo when handling DLQ flows.\n",
    )
    _write(
        a / "skills" / "bar.md",
        "---\nsee_also: [gates:README]\n---\n"
        "# Skill: Bar - when adding a new connector\nSteps: 1. ... 2. ... 3. ...\n",
    )
    _write(
        a / "workflows" / "bug-fix.md",
        "---\ntriggers: [bug, fix a bug, debug]\ngates: [verify-java]\n"
        "see_also: [skill:bar, pattern:foo, core:guardrails]\n---\n"
        "# Workflow: bug-fix - Proj A\nReproduce, isolate, fix, test.\n",
    )
    _write(
        a / "gates" / "README.md",
        "# Gates - Proj A\nverify-java.sh runs lint + tests + security.\n",
    )
    script = a / "gates" / "scripts" / "verify-java.sh"
    _write(script, "#!/usr/bin/env bash\nset -euo pipefail\necho ok\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    b = tmp_path / "proj-b"
    _write(b / "AGENTS.md", "# AGENTS.md - Proj B\nDifferent stack.\n")
    _write(b / "core" / "guardrails.md", "# Guardrails - Proj B\n")
    _write(b / "core" / "definition-of-done.md", "# DoD - Proj B\n")
    _write(b / "core" / "glossary.md", "# Glossary - Proj B\n")
    _write(b / "architecture" / "overview.md", "# Architecture - Proj B\n")
    _write(
        b / "languages" / "shell-yaml" / "standards.md",
        "# shell-yaml standards - Proj B\nUse shellcheck.\n",
    )
    _write(b / "stray.md", "# Stray\nThis is not a recognized doc type.\n")

    _write(tmp_path / "README.md", "# repo readme - should be excluded\n")
    _write(tmp_path / "loose.md", "# loose - should be skipped with warning\n")
    return tmp_path
