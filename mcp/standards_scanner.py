"""
standards_scanner.py - Lightweight scanner for the standards corpus.

Reads file rows from a StandardsStore (SQLite), parses YAML frontmatter from
each markdown row, and runs validation rules. Returns ProjectStatus /
FileStatus dataclasses consumed by the dashboard Standards pages.

No dependency on the deleted loader / corpus / search infrastructure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from standards_store import FileRow, StandardsStore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEVERITY_HARD = "hard"
SEVERITY_SOFT = "soft"

# Required files every standards project must have.
REQUIRED_FILES = (
    "AGENTS.md",
    "ARCHITECTURE.md",
    "core/guardrails.md",
    "core/git.md",
    "core/definition-of-done.md",
    "core/glossary.md",
    "gates/README.md",
)

REQUIRED_WORKFLOWS = ("new-feature", "bug-fix", "security-fix", "refactor")

# Minimum useful content length (chars) after stripping frontmatter.
_MIN_CONTENT_LEN = 80

_FENCED_CODE_RE = re.compile(r"^```", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class RuleResult:
    rule_id: str
    severity: str  # "hard" | "soft"
    passed: bool
    message: str


@dataclass
class FileStatus:
    relative_path: str
    name: str
    indicator: str  # "red" | "amber" | "green"
    rules: list[RuleResult]
    title: str = ""
    summary: str = ""
    raw_body: str = ""  # post-frontmatter markdown source (empty for scripts)
    frontmatter: dict = field(default_factory=dict)  # parsed YAML (empty for scripts)

    @property
    def passed(self) -> list[RuleResult]:
        return [r for r in self.rules if r.passed]

    @property
    def failed(self) -> list[RuleResult]:
        return [r for r in self.rules if not r.passed]

    @property
    def hard_failures(self) -> list[RuleResult]:
        return [r for r in self.failed if r.severity == SEVERITY_HARD]

    @property
    def soft_failures(self) -> list[RuleResult]:
        return [r for r in self.failed if r.severity != SEVERITY_HARD]


@dataclass
class ProjectStatus:
    project: str
    indicator: str
    files: list[FileStatus]
    missing_required: list[str]
    rule_results: list[RuleResult]
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_files(self) -> int:
        return len(self.files)


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Return (metadata_dict, body_without_frontmatter)."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    body = content[m.end() :]
    return meta, body


def _extract_title(meta: dict, body: str, filename: str) -> str:
    t = meta.get("title")
    if isinstance(t, str) and t.strip():
        return t.strip()
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
        if s and not s.startswith("---"):
            break
    return filename


def _extract_description(meta: dict) -> str:
    desc = meta.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()[:160]
    return ""


# ---------------------------------------------------------------------------
# Per-file rules
# ---------------------------------------------------------------------------


def _run_file_rules(rel_path: str, meta: dict, body: str, is_executable: bool) -> list[RuleResult]:
    """Run the per-file rule set.

    `is_executable` matters only for rows under gates/scripts/ - it comes
    from the stored `is_executable` flag rather than a filesystem stat().
    """
    results: list[RuleResult] = []

    # Hard: frontmatter has title
    has_title = bool(meta.get("title"))
    results.append(
        RuleResult(
            rule_id="fm-title",
            severity=SEVERITY_HARD,
            passed=has_title,
            message="YAML frontmatter has title"
            if has_title
            else "Missing title in YAML frontmatter",
        )
    )

    # Hard: frontmatter has description
    has_desc = bool(meta.get("description"))
    results.append(
        RuleResult(
            rule_id="fm-description",
            severity=SEVERITY_HARD,
            passed=has_desc,
            message=(
                "YAML frontmatter has description"
                if has_desc
                else "Missing description in YAML frontmatter"
            ),
        )
    )

    # Soft: minimum content length
    long_enough = len(body.strip()) >= _MIN_CONTENT_LEN
    results.append(
        RuleResult(
            rule_id="content-length",
            severity=SEVERITY_SOFT,
            passed=long_enough,
            message=(
                f"Content is {len(body.strip())} chars (≥ {_MIN_CONTENT_LEN})"
                if long_enough
                else f"Content too short ({len(body.strip())} chars, need ≥ {_MIN_CONTENT_LEN})"
            ),
        )
    )

    # Soft: has structured content (code blocks or tables)
    has_code = bool(_FENCED_CODE_RE.search(body))
    has_table = bool(_TABLE_ROW_RE.search(body))
    has_structure = has_code or has_table
    results.append(
        RuleResult(
            rule_id="structured-content",
            severity=SEVERITY_SOFT,
            passed=has_structure,
            message=(
                "Contains code blocks or tables"
                if has_structure
                else "No code blocks or tables found"
            ),
        )
    )

    # Soft: gate scripts referenced in .md under gates/scripts/
    if rel_path.startswith("gates/scripts/"):
        results.append(
            RuleResult(
                rule_id="gate-executable",
                severity=SEVERITY_SOFT,
                passed=is_executable,
                message=(
                    "Gate script is executable"
                    if is_executable
                    else "Gate script is not executable (chmod +x)"
                ),
            )
        )

    return results


# ---------------------------------------------------------------------------
# Project scanner
# ---------------------------------------------------------------------------


def _indicator(results: list[RuleResult]) -> str:
    if any(r.severity == SEVERITY_HARD and not r.passed for r in results):
        return "red"
    if any(r.severity == SEVERITY_SOFT and not r.passed for r in results):
        return "amber"
    return "green"


def _file_status(row: FileRow) -> FileStatus:
    if row.kind == "markdown":
        meta: dict = {}
        if row.frontmatter.strip():
            try:
                loaded = yaml.safe_load(row.frontmatter)
            except yaml.YAMLError:
                loaded = None
            if isinstance(loaded, dict):
                meta = loaded
        body = row.body
        title = row.title or _extract_title(meta, body, row.relative_path.rsplit("/", 1)[-1])
        desc = row.description or _extract_description(meta)
        rules = _run_file_rules(row.relative_path, meta, body, row.is_executable)
        return FileStatus(
            relative_path=row.relative_path,
            name=row.relative_path.rsplit("/", 1)[-1],
            indicator=_indicator(rules),
            rules=rules,
            title=title,
            summary=desc,
            raw_body=body,
            frontmatter=meta,
        )

    # Gate scripts (non-markdown rows): a single executable-bit rule, as before.
    rules = [
        RuleResult(
            rule_id="gate-executable",
            severity=SEVERITY_SOFT,
            passed=row.is_executable,
            message=(
                "Gate script is executable"
                if row.is_executable
                else "Gate script is not executable (chmod +x)"
            ),
        )
    ]
    name = row.relative_path.rsplit("/", 1)[-1]
    return FileStatus(
        relative_path=row.relative_path,
        name=name,
        indicator="green" if row.is_executable else "amber",
        rules=rules,
        title=name,
        summary=f"Gate script: {name}",
        raw_body=row.body,
    )


async def scan_project(store: StandardsStore, project_name: str) -> ProjectStatus:
    """Scan a single standards project and return its health status."""
    rows = await store.list_files(project_name)
    present_paths = {r.relative_path for r in rows}

    missing: list[str] = []
    all_results: list[RuleResult] = []

    for req in REQUIRED_FILES:
        present = req in present_paths
        if not present:
            missing.append(req)
        all_results.append(
            RuleResult(
                rule_id="required-file",
                severity=SEVERITY_HARD,
                passed=present,
                message=f"`{req}` {'exists' if present else 'is missing'}",
            )
        )

    for wf in REQUIRED_WORKFLOWS:
        wf_path = f"workflows/{wf}.md"
        present = wf_path in present_paths
        if not present:
            missing.append(wf_path)
        all_results.append(
            RuleResult(
                rule_id="required-workflow",
                severity=SEVERITY_HARD,
                passed=present,
                message=f"Workflow `{wf}` {'exists' if present else 'is missing'}",
            )
        )

    files = [_file_status(row) for row in rows]

    counts = {
        "red": sum(1 for f in files if f.indicator == "red"),
        "amber": sum(1 for f in files if f.indicator == "amber"),
        "green": sum(1 for f in files if f.indicator == "green"),
    }

    if missing or counts["red"] > 0:
        proj_indicator = "red"
    elif counts["amber"] > 0:
        proj_indicator = "amber"
    else:
        proj_indicator = "green"

    return ProjectStatus(
        project=project_name,
        indicator=proj_indicator,
        files=files,
        missing_required=missing,
        rule_results=all_results,
        counts=counts,
    )


async def scan_all(store: StandardsStore) -> list[ProjectStatus]:
    """Scan every project registered in the store."""
    projects = await store.list_projects()
    return [await scan_project(store, name) for name in projects]
