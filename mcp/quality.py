"""
quality.py - Score the per-project standards corpus for the dashboard.

Pure functions over the in-memory `RulesStore`. Used by the Projects
pages in the dashboard. See `quality_rules.py` for the rule library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from loader import RuleDoc, RulesStore
from quality_rules import (
    REQUIRED_FILES,
    REQUIRED_WORKFLOWS,
    SEVERITY_HARD,
    UNIVERSAL_RULES,
    RuleContext,
    RuleResult,
    rules_for,
)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class FileStatus:
    relative_path: str
    doc_type: str
    name: str
    indicator: str  # "red" | "amber" | "green"
    rules: list[RuleResult]  # all rules that ran, passed and failed
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
    files: list[FileStatus]  # sorted by display order
    missing_required: list[str]  # required paths absent on disk
    rule_results: list[RuleResult]  # project-level rules (rollup)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_files(self) -> int:
        return len(self.files)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _indicator_from_results(results: list[RuleResult]) -> str:
    has_hard = any(r.severity == SEVERITY_HARD and not r.passed for r in results)
    if has_hard:
        return "red"
    has_soft = any(r.severity != SEVERITY_HARD and not r.passed for r in results)
    return "amber" if has_soft else "green"


def _doc_title(doc: RuleDoc) -> str:
    t = doc.metadata.get("title")
    if isinstance(t, str) and t.strip():
        return t.strip()
    for line in doc.content.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
        if s and not s.startswith("---"):
            break
    return doc.name


def _doc_summary(doc: RuleDoc, max_len: int = 160) -> str:
    desc = doc.metadata.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()[:max_len]
    for line in doc.content.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(">") or s.startswith("---"):
            continue
        return s[:max_len]
    return ""


# Display order - group by folder, then alphabetical within group.
_DISPLAY_ORDER = (
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


def _sort_key(fs: FileStatus) -> tuple[int, str]:
    try:
        bucket = _DISPLAY_ORDER.index(fs.doc_type)
    except ValueError:
        bucket = len(_DISPLAY_ORDER)
    return (bucket, fs.relative_path)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_doc(doc: RuleDoc, ctx: RuleContext) -> FileStatus:
    rules = list(UNIVERSAL_RULES) + list(rules_for(doc))
    results: list[RuleResult] = []
    for rule in rules:
        try:
            results.append(rule(doc, ctx))
        except Exception as exc:  # noqa: BLE001
            results.append(
                RuleResult(
                    rule_id=getattr(rule, "__name__", "unknown"),
                    severity=SEVERITY_HARD,
                    passed=False,
                    message=f"Rule crashed: {exc}",
                )
            )
    indicator = _indicator_from_results(results)
    return FileStatus(
        relative_path=doc.relative_path,
        doc_type=doc.doc_type,
        name=doc.name,
        indicator=indicator,
        rules=results,
        title=_doc_title(doc),
        summary=_doc_summary(doc),
    )


def _project_level_rules(
    project: str,
    project_files: list[RuleDoc],
    project_root: Path,
    file_statuses: list[FileStatus],
) -> tuple[list[RuleResult], list[str]]:
    """Run rules that apply to the project as a whole (not any single doc).

    Returns (results, missing_required).
    """
    on_disk = {
        (project_root / rel).relative_to(project_root).as_posix(): (project_root / rel)
        for rel in REQUIRED_FILES
    }
    missing: list[str] = []
    for rel, p in on_disk.items():
        if not p.is_file():
            missing.append(rel)

    # Required workflows
    for wf in REQUIRED_WORKFLOWS:
        rel = f"workflows/{wf}.md"
        if not (project_root / rel).is_file():
            missing.append(rel)

    # ≥1 languages/<lang>/standards.md
    langs_dir = project_root / "languages"
    has_standards = False
    if langs_dir.is_dir():
        for lang_dir in langs_dir.iterdir():
            if (lang_dir / "standards.md").is_file():
                has_standards = True
                break
    if not has_standards:
        missing.append("languages/<lang>/standards.md")

    results: list[RuleResult] = []

    results.append(
        RuleResult(
            rule_id="project.required_files",
            severity=SEVERITY_HARD,
            passed=not missing,
            message=(
                "All required files present"
                if not missing
                else f"Missing required files: {', '.join(missing)}"
            ),
        )
    )

    # No 'other' doc types
    others = [d for d in project_files if d.doc_type == "other"]
    results.append(
        RuleResult(
            rule_id="project.no_other_doc_types",
            severity=SEVERITY_HARD,
            passed=not others,
            message=(
                "Every indexed file matches the layout"
                if not others
                else f"Files outside the layout: {', '.join(d.relative_path for d in others)}"
            ),
        )
    )

    # Soft: at least one ADR
    adrs = [d for d in project_files if d.doc_type == "architecture-decision"]
    results.append(
        RuleResult(
            rule_id="project.has_adr",
            severity="soft",
            passed=bool(adrs),
            message=(
                f"Has {len(adrs)} ADR(s) under architecture/decisions/"
                if adrs
                else "No ADRs yet - encourage the team to record decisions"
            ),
        )
    )

    # Soft: no INDEX drift (rolled up from the index file's own result)
    index_file = next((fs for fs in file_statuses if fs.doc_type == "index"), None)
    if index_file is not None:
        drift_failed = any(
            not r.passed and r.rule_id == "index.up_to_date" for r in index_file.rules
        )
        results.append(
            RuleResult(
                rule_id="project.index_fresh",
                severity="soft",
                passed=not drift_failed,
                message=(
                    "INDEX.md is up to date"
                    if not drift_failed
                    else "INDEX.md is stale - run `python scripts/validate-rules.py --regen-index`"
                ),
            )
        )

    return results, missing


def score_project(
    project: str,
    store: RulesStore,
    project_root: Path,
) -> ProjectStatus:
    project_files = store.for_project(project)

    # Read INDEX.md from disk once and pass into RuleContext.
    index_path = project_root / "INDEX.md"
    on_disk_index = index_path.read_text(encoding="utf-8") if index_path.is_file() else None

    ctx = RuleContext(
        project_root=project_root,
        project_files=project_files,
        on_disk_index=on_disk_index,
    )

    file_statuses = [score_doc(doc, ctx) for doc in project_files]
    file_statuses.sort(key=_sort_key)

    project_results, missing = _project_level_rules(
        project, project_files, project_root, file_statuses
    )

    # Project indicator = worst of (per-file indicators, project-level rules).
    file_results = [
        RuleResult(
            rule_id=f"file.{fs.relative_path}.{r.rule_id}",
            severity=r.severity,
            passed=r.passed,
            message=r.message,
        )
        for fs in file_statuses
        for r in fs.rules
    ]
    indicator = _indicator_from_results(file_results + project_results)

    counts = {
        "red": sum(1 for fs in file_statuses if fs.indicator == "red"),
        "amber": sum(1 for fs in file_statuses if fs.indicator == "amber"),
        "green": sum(1 for fs in file_statuses if fs.indicator == "green"),
    }

    return ProjectStatus(
        project=project,
        indicator=indicator,
        files=file_statuses,
        missing_required=missing,
        rule_results=project_results,
        counts=counts,
    )
