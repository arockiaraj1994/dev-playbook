"""
loader.py - Loads markdown docs from the standards root into memory.

Path-first dispatch: doc_type is inferred from the doc's path inside the
project directory, not its filename alone. The corpus is described by
CorpusSpec (see corpus.py):

    standards/<project>/
      AGENTS.md, INDEX.md, core/, architecture/, languages/, ...
"""

from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from corpus import (
    KNOWN_DOC_TYPES,
    CorpusSpec,
    infer_standards_type,
    standards_spec,
)

logger = logging.getLogger(__name__)

# Re-export for callers / validator
__all__ = [
    "KNOWN_DOC_TYPES",
    "SEE_ALSO_KINDS",
    "SEE_ALSO_TOOLS",
    "SEE_ALSO_CORE",
    "RuleDoc",
    "RulesStore",
    "DocStore",
    "resolve_rules_root",
    "resolve_standards_root",
    "parse_corpus",
    "_parse_docs",
    "_parse_frontmatter",
    "_infer_doc_type_and_name",
    "_infer_doc_type",
    "_is_excluded",
    "load_store",
    "bootstrap_all",
]

# ---------------------------------------------------------------------------
# Config / see_also
# ---------------------------------------------------------------------------

_MCP_DIR = Path(__file__).resolve().parent
_REPO = _MCP_DIR.parent

# Legacy exclusions kept only for the back-compat shim path (repo-root walk).
_EXCLUDED_PREFIXES = (
    "mcp/",
    ".git/",
    ".github/",
    "scripts/",
    ".venv/",
    "standards/",
    "docs/",
)
_EXCLUDED_TOP_LEVEL_FILES = {
    "README.md",
    "CONTRIBUTING.md",
    "TEMPLATE.md",
    "CHANGELOG.md",
}
_EXCLUDED_PROJECT_FILES = {"README.md"}

# Recognized `<kind>` values in a doc's `see_also:` / `targets:` frontmatter.
SEE_ALSO_KINDS = (
    "tool",
    "pattern",
    "skill",
    "workflow",
    "gate",
    "gates",  # tolerated alias of `gate`
    "language",
    "architecture",
    "core",
    "agents",
)

# The v0.8.0 tool surface. Earlier names are NOT accepted in frontmatter -
# v0.8.0 is a clean break, and scripts/validate-rules.py fails a corpus that
# still names a removed tool rather than silently rendering nothing.
SEE_ALSO_TOOLS = (
    "playbook_start",
    "playbook_get",
    "playbook_find",
)

SEE_ALSO_CORE = ("guardrails", "definition-of-done")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RuleDoc:
    project: str
    relative_path: str
    doc_type: str
    name: str
    content: str
    metadata: dict = field(default_factory=dict)


@dataclass
class DocStore:
    """In-memory document store for the standards corpus."""

    docs: list[RuleDoc] = field(default_factory=list)

    def projects(self) -> list[str]:
        return sorted({d.project for d in self.docs})

    def get(self, project: str, relative_path: str) -> RuleDoc | None:
        for doc in self.docs:
            if doc.project == project and doc.relative_path == relative_path:
                return doc
        return None

    def for_project(self, project: str) -> list[RuleDoc]:
        return [d for d in self.docs if d.project == project]

    def of_type(self, project: str, doc_type: str) -> list[RuleDoc]:
        return [d for d in self.docs if d.project == project and d.doc_type == doc_type]

    def all_docs(self) -> list[RuleDoc]:
        return list(self.docs)

    def replace_all(self, fresh: list[RuleDoc]) -> None:
        """Atomic swap of the whole corpus (used by the manual reload)."""
        self.docs = fresh


# Back-compat alias
RulesStore = DocStore


# ---------------------------------------------------------------------------
# Frontmatter (small, dependency-free subset)
# ---------------------------------------------------------------------------


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_inline_list(raw: str) -> list[str]:
    """Parse `[a, b, "c d"]` into ["a", "b", "c d"]. Bare strings allowed."""
    inner = raw.strip()[1:-1]
    if not inner.strip():
        return []
    items: list[str] = []
    buf: list[str] = []
    in_quote: str | None = None
    for ch in inner:
        if in_quote:
            buf.append(ch)
            if ch == in_quote:
                in_quote = None
        elif ch in ("'", '"'):
            in_quote = ch
            buf.append(ch)
        elif ch == ",":
            items.append(_strip_quotes("".join(buf)))
            buf = []
        else:
            buf.append(ch)
    if buf:
        items.append(_strip_quotes("".join(buf)))
    return [i for i in items if i]


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Parse optional YAML frontmatter at the top of `content`.

    Returns (metadata_dict, body_without_frontmatter). If no frontmatter
    is present (or it's malformed), returns ({}, original content).
    """
    if not content.startswith("---"):
        return {}, content

    after_open = content[3:]
    m = re.search(r"^---\s*$", after_open, re.MULTILINE)
    if not m:
        return {}, content

    raw_block = after_open[: m.start()]
    body_start = 3 + m.end()
    if body_start < len(content) and content[body_start] == "\n":
        body_start += 1
    body = content[body_start:]

    metadata: dict = {}
    for line_no, raw_line in enumerate(raw_block.splitlines(), start=1):
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            logger.debug("frontmatter line %d ignored (no colon): %r", line_no, raw_line)
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith("[") and value.endswith("]"):
            metadata[key] = _parse_inline_list(value)
        elif not value:
            metadata[key] = ""
        else:
            metadata[key] = _strip_quotes(value)
    return metadata, body


# ---------------------------------------------------------------------------
# Path-first doc-type inference (standards - public alias)
# ---------------------------------------------------------------------------


def _infer_doc_type_and_name(relative_path: str) -> tuple[str, str]:
    return infer_standards_type(relative_path)


def _infer_doc_type(relative_path: str) -> str:
    return _infer_doc_type_and_name(relative_path)[0]


# ---------------------------------------------------------------------------
# Exclusions (legacy shim only)
# ---------------------------------------------------------------------------


def _is_excluded(relative_path: str) -> bool:
    for prefix in _EXCLUDED_PREFIXES:
        if relative_path.startswith(prefix):
            return True
    if relative_path in _EXCLUDED_TOP_LEVEL_FILES:
        return True
    parts = Path(relative_path).parts
    if len(parts) == 2 and parts[1] in _EXCLUDED_PROJECT_FILES:
        return True
    return False


# ---------------------------------------------------------------------------
# Walk + parse
# ---------------------------------------------------------------------------


def _list_gate_scripts(project_root: Path) -> list[str]:
    scripts_dir = project_root / "gates" / "scripts"
    if not scripts_dir.is_dir():
        return []
    return sorted(p.name for p in scripts_dir.iterdir() if p.is_file())


def parse_corpus(spec: CorpusSpec) -> list[RuleDoc]:
    """Walk a corpus root and load all .md files into RuleDoc list."""
    root = spec.root
    if not root.is_dir():
        logger.info("Corpus root %s does not exist; loading 0 docs.", root)
        return []

    docs: list[RuleDoc] = []
    for md_file in root.rglob("*.md"):
        relative = md_file.relative_to(root)
        relative_str = relative.as_posix()

        parts = relative.parts
        if len(parts) < 2:
            logger.warning(
                "Skipping top-level markdown file %s (not under a project directory)",
                relative_str,
            )
            continue

        project = parts[0]
        doc_relative = "/".join(parts[1:])

        # Per-project excluded files (e.g. README.md)
        if Path(doc_relative).name in spec.excluded_files and Path(doc_relative).parts == (
            Path(doc_relative).name,
        ):
            continue
        if doc_relative in spec.excluded_files:
            continue

        doc_type, name = spec.infer(doc_relative)

        try:
            raw_content = md_file.read_text(encoding="utf-8")
        except OSError as e:
            logger.error("Failed to read %s: %s", md_file, e)
            continue

        metadata, body = _parse_frontmatter(raw_content)

        if doc_type == "language-rules":
            lang_parts = Path(doc_relative).parts
            if len(lang_parts) >= 2 and lang_parts[0] == "languages":
                metadata["language"] = lang_parts[1]

        if doc_type == "gate":
            scripts = _list_gate_scripts(root / project)
            if scripts:
                metadata["gate_scripts"] = scripts

        if doc_type == "other":
            logger.warning(
                "Doc %s/%s/%s does not match the expected layout; "
                "indexing as 'other' (validator will flag this).",
                spec.name,
                project,
                doc_relative,
            )

        docs.append(
            RuleDoc(
                project=project,
                relative_path=doc_relative,
                doc_type=doc_type,
                name=name,
                content=body,
                metadata=metadata,
            )
        )
        logger.debug("Loaded [%s]: %s / %s (%s)", spec.name, project, doc_relative, doc_type)

    return docs


def _parse_docs(repo_root: Path) -> list[RuleDoc]:
    """Legacy entry: treat `repo_root` as a standards corpus root.

    Used by tests and the validator when pointing at an explicit root.
    """
    spec = CorpusSpec(name="standards", root=repo_root, infer=infer_standards_type)
    return parse_corpus(spec)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_standards_root() -> Path:
    """Resolve the standards corpus root with a 2-release back-compat shim.

    Preference order:
      1. MCP_STANDARDS_ROOT (via standards_spec)
      2. <repo>/standards if it exists
      3. <repo> itself (legacy: projects next to mcp/) + deprecation warning
    """
    spec = standards_spec()
    if spec.root.is_dir():
        # If standards/ exists but is empty and legacy layout still has projects,
        # still prefer standards/ (explicit).
        return spec.root

    # Back-compat: standards/ missing → fall back to repo root
    legacy = _REPO
    if legacy.is_dir():
        warnings.warn(
            "MCP_STANDARDS_ROOT unset and standards/ missing; "
            "falling back to legacy repo-root layout. "
            "Move projects under standards/ (deprecated; remove in 2 releases).",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.warning(
            "Deprecation: loading standards from repo root %s "
            "(move to standards/ or set MCP_STANDARDS_ROOT).",
            legacy,
        )
        return legacy

    raise FileNotFoundError(
        f"Standards root does not exist: {spec.root} (and legacy {_REPO} missing)",
    )


def resolve_rules_root() -> Path:
    """Alias for resolve_standards_root() - kept for existing callers."""
    return resolve_standards_root()


def load_store(repo_root: Path) -> DocStore:
    """Load docs from an explicit root (used by tests and the validator)."""
    spec = CorpusSpec(name="standards", root=repo_root, infer=infer_standards_type)
    docs = parse_corpus(spec)
    logger.info("Loaded %d docs from %s.", len(docs), repo_root)
    return DocStore(docs=docs)


def bootstrap_all() -> DocStore:
    """Load the standards corpus into a DocStore."""
    docs = parse_corpus(standards_spec())
    logger.info("Loaded %d standards docs.", len(docs))
    return DocStore(docs=docs)
