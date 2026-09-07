"""
corpus.py - CorpusSpec for the standards/ root.

One env var makes a future repo split a config change, not a rewrite:
  MCP_STANDARDS_ROOT - default <repo>/standards
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_MCP_DIR = Path(__file__).resolve().parent
_REPO = _MCP_DIR.parent


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return default


def infer_standards_type(relative_path: str) -> tuple[str, str]:
    """Infer (doc_type, name) for a path inside a standards project directory."""
    parts = Path(relative_path).parts
    stem = Path(relative_path).stem

    if relative_path == "AGENTS.md":
        return "agents", "agents"
    if relative_path == "INDEX.md":
        return "index", "index"

    if len(parts) < 2:
        return "other", stem

    head = parts[0]

    if head == "core":
        if relative_path == "core/guardrails.md":
            return "guardrails", "guardrails"
        if relative_path == "core/definition-of-done.md":
            return "definition-of-done", "definition-of-done"
        if relative_path == "core/glossary.md":
            return "glossary", "glossary"
        return "other", stem

    if head == "architecture":
        if relative_path == "architecture/overview.md":
            return "architecture", "overview"
        if len(parts) >= 3 and parts[1] == "decisions":
            return "architecture-decision", stem
        return "other", stem

    if head == "languages":
        if len(parts) == 3:
            lang = parts[1]
            return "language-rules", f"{lang}/{stem}"
        return "other", stem

    if head == "patterns":
        return "pattern", stem

    if head == "skills":
        return "skill", stem

    if head == "workflows":
        return "workflow", stem

    if head == "gates":
        if relative_path == "gates/README.md":
            return "gate", "gate"
        return "other", stem

    return "other", stem


KNOWN_DOC_TYPES = (
    "agents",
    "index",
    "guardrails",
    "definition-of-done",
    "glossary",
    "architecture",
    "architecture-decision",
    "language-rules",
    "pattern",
    "skill",
    "workflow",
    "gate",
)


@dataclass(frozen=True)
class CorpusSpec:
    name: str
    root: Path
    infer: Callable[[str], tuple[str, str]]
    excluded_files: frozenset[str] = field(default_factory=lambda: frozenset({"README.md"}))


def standards_spec() -> CorpusSpec:
    return CorpusSpec(
        name="standards",
        root=_env_path("MCP_STANDARDS_ROOT", _REPO / "standards"),
        infer=infer_standards_type,
    )


# Module-level default resolved at import (env can still override via factory).
STANDARDS = standards_spec()
