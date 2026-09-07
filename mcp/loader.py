"""
loader.py - Loads markdown docs from a corpus root into memory.

Path-first dispatch: doc_type is inferred from the doc's path inside the
project directory, not its filename alone. Corpora are described by
CorpusSpec (see corpus.py):

    standards/<project>/
      AGENTS.md, INDEX.md, core/, architecture/, languages/, ...

    requirements/<project>/
      AGENTS.md, INDEX.md, workflows/, PRD-*/prd.md, PRD-*/stories/ST-*.md
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
    requirements_spec,
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
    "requirements/",
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
    "req",
    "requirement",  # tolerated alias of `req`
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
    corpus: str = "standards"


@dataclass
class DocStore:
    """Corpus-aware in-memory document store."""

    docs: list[RuleDoc] = field(default_factory=list)

    def projects(self, corpus: str | None = None) -> list[str]:
        if corpus is None:
            return sorted({d.project for d in self.docs})
        return sorted({d.project for d in self.docs if d.corpus == corpus})

    def get(
        self,
        project: str,
        relative_path: str,
        corpus: str | None = None,
    ) -> RuleDoc | None:
        for doc in self.docs:
            if doc.project == project and doc.relative_path == relative_path:
                if corpus is None or doc.corpus == corpus:
                    return doc
        return None

    def for_project(self, project: str, corpus: str | None = None) -> list[RuleDoc]:
        return [
            d for d in self.docs if d.project == project and (corpus is None or d.corpus == corpus)
        ]

    def of_type(
        self,
        project: str,
        doc_type: str,
        corpus: str | None = None,
    ) -> list[RuleDoc]:
        return [
            d
            for d in self.docs
            if d.project == project
            and d.doc_type == doc_type
            and (corpus is None or d.corpus == corpus)
        ]

    def all_docs(self, corpus: str | None = None) -> list[RuleDoc]:
        if corpus is None:
            return list(self.docs)
        return [d for d in self.docs if d.corpus == corpus]

    def find_by_id(
        self,
        corpus: str,
        project: str,
        req_id: str,
    ) -> RuleDoc | None:
        """Look up a PRD or story by frontmatter id (e.g. PRD-003 / ST-114)."""
        req_id = req_id.strip()
        for doc in self.docs:
            if doc.corpus != corpus or doc.project != project:
                continue
            if doc.doc_type not in ("prd", "story"):
                continue
            meta_id = doc.metadata.get("id")
            if isinstance(meta_id, str) and meta_id.strip() == req_id:
                return doc
            if doc.name == req_id:
                return doc
        return None

    def stories_of(self, prd: RuleDoc) -> list[RuleDoc]:
        """Stories under the same PRD folder (path siblings)."""
        if prd.doc_type != "prd":
            return []
        # relative_path like PRD-003-offline-sync/prd.md
        folder = Path(prd.relative_path).parent.as_posix()
        prefix = f"{folder}/stories/"
        return sorted(
            [
                d
                for d in self.docs
                if d.corpus == prd.corpus
                and d.project == prd.project
                and d.doc_type == "story"
                and d.relative_path.startswith(prefix)
            ],
            key=lambda d: d.name,
        )

    def prd_of(self, story: RuleDoc) -> RuleDoc | None:
        """Parent PRD via path arithmetic: ../../prd.md from stories/."""
        if story.doc_type != "story":
            return None
        # PRD-003-offline-sync/stories/ST-114-*.md → PRD-003-offline-sync/prd.md
        parts = Path(story.relative_path).parts
        if len(parts) < 3:
            return None
        prd_rel = f"{parts[0]}/prd.md"
        return self.get(story.project, prd_rel, corpus=story.corpus)

    def replace_corpus(self, corpus: str, fresh: list[RuleDoc]) -> None:
        """Atomic swap of one corpus's docs (used by TTL cache)."""
        others = [d for d in self.docs if d.corpus != corpus]
        self.docs = others + fresh


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

        # Prefer frontmatter id for PRD/story names
        meta_id = metadata.get("id")
        if isinstance(meta_id, str) and meta_id.strip() and doc_type in ("prd", "story"):
            name = meta_id.strip()

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
                corpus=spec.name,
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
    spec = CorpusSpec(
        name="standards",
        root=repo_root,
        cache_policy="boot",
        infer=infer_standards_type,
    )
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


def load_store(repo_root: Path, *, corpus: str = "standards") -> DocStore:
    """Load docs from an explicit root as the given corpus name."""
    if corpus == "requirements":
        infer = requirements_spec().infer
    else:
        infer = infer_standards_type
    spec = CorpusSpec(
        name=corpus,
        root=repo_root,
        cache_policy="boot",
        infer=infer,
    )
    docs = parse_corpus(spec)
    logger.info("Loaded %d %s docs from %s.", len(docs), corpus, repo_root)
    return DocStore(docs=docs)


def bootstrap_all() -> DocStore:
    """Load both standards and requirements into one DocStore."""
    std = parse_corpus(standards_spec())
    req_spec = requirements_spec()
    req = parse_corpus(req_spec) if req_spec.root.is_dir() else []
    logger.info(
        "Loaded %d standards + %d requirements docs.",
        len(std),
        len(req),
    )
    return DocStore(docs=std + req)
