"""
standards_scanner.py - Lightweight scanner for the standards/ corpus.

Walks the standards/ directory on disk, parses YAML frontmatter from each
.md file, and runs validation rules.  Returns ProjectStatus / FileStatus
dataclasses consumed by the dashboard Standards pages.

No dependency on the deleted loader / corpus / search infrastructure.
"""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEVERITY_HARD = "hard"
SEVERITY_SOFT = "soft"

# Required files every standards project must have.
REQUIRED_FILES = (
    "AGENTS.md",
    "core/guardrails.md",
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
    body = content[m.end():]
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


def _run_file_rules(
    rel_path: str, meta: dict, body: str, project_root: Path
) -> list[RuleResult]:
    results: list[RuleResult] = []

    # Hard: frontmatter has title
    has_title = bool(meta.get("title"))
    results.append(
        RuleResult(
            rule_id="fm-title",
            severity=SEVERITY_HARD,
            passed=has_title,
            message="YAML frontmatter has title" if has_title else "Missing title in YAML frontmatter",
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
        full = project_root / rel_path
        is_exec = full.exists() and bool(full.stat().st_mode & stat.S_IXUSR)
        results.append(
            RuleResult(
                rule_id="gate-executable",
                severity=SEVERITY_SOFT,
                passed=is_exec,
                message=(
                    "Gate script is executable"
                    if is_exec
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


def scan_project(project_name: str, project_root: Path) -> ProjectStatus:
    """Scan a single standards project directory and return its health status."""
    all_results: list[RuleResult] = []
    files: list[FileStatus] = []

    # Check required files
    missing: list[str] = []
    for req in REQUIRED_FILES:
        if not (project_root / req).is_file():
            missing.append(req)

    # Check required workflows
    for wf in REQUIRED_WORKFLOWS:
        wf_path = f"workflows/{wf}.md"
        if not (project_root / wf_path).is_file():
            missing.append(wf_path)

    # Project-level rule results
    for req in REQUIRED_FILES:
        present = (project_root / req).is_file()
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
        present = (project_root / wf_path).is_file()
        all_results.append(
            RuleResult(
                rule_id="required-workflow",
                severity=SEVERITY_HARD,
                passed=present,
                message=f"Workflow `{wf}` {'exists' if present else 'is missing'}",
            )
        )

    # Walk all .md files
    for md_file in sorted(project_root.rglob("*.md")):
        if not md_file.is_file():
            continue
        rel = str(md_file.relative_to(project_root))

        try:
            content = md_file.read_text(encoding="utf-8")
        except OSError:
            continue

        meta, body = _parse_frontmatter(content)
        title = _extract_title(meta, body, md_file.name)
        desc = _extract_description(meta)
        file_rules = _run_file_rules(rel, meta, body, project_root)

        file_ind = _indicator(file_rules)
        files.append(
            FileStatus(
                relative_path=rel,
                name=md_file.name,
                indicator=file_ind,
                rules=file_rules,
                title=title,
                summary=desc,
            )
        )

    # Walk gate scripts (non-.md files under gates/scripts/)
    gate_scripts_dir = project_root / "gates" / "scripts"
    if gate_scripts_dir.is_dir():
        for script_file in sorted(gate_scripts_dir.iterdir()):
            if script_file.is_file() and not script_file.name.endswith(".md"):
                rel = str(script_file.relative_to(project_root))
                is_exec = bool(script_file.stat().st_mode & stat.S_IXUSR)
                rules = [
                    RuleResult(
                        rule_id="gate-executable",
                        severity=SEVERITY_SOFT,
                        passed=is_exec,
                        message=(
                            "Gate script is executable"
                            if is_exec
                            else "Gate script is not executable (chmod +x)"
                        ),
                    )
                ]
                files.append(
                    FileStatus(
                        relative_path=rel,
                        name=script_file.name,
                        indicator="green" if is_exec else "amber",
                        rules=rules,
                        title=script_file.name,
                        summary=f"Gate script: {script_file.name}",
                    )
                )

    # Aggregate counts
    counts = {
        "red": sum(1 for f in files if f.indicator == "red"),
        "amber": sum(1 for f in files if f.indicator == "amber"),
        "green": sum(1 for f in files if f.indicator == "green"),
    }

    # Overall project indicator
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


def scan_all(standards_root: Path) -> list[ProjectStatus]:
    """Scan all project directories under the standards root."""
    if not standards_root.is_dir():
        return []
    projects = []
    for child in sorted(standards_root.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            projects.append(scan_project(child.name, child))
    return projects
