"""
quality_rules.py - Static rule library for the Projects dashboard.

Each `Rule` evaluates a `RuleDoc` (and optional project context) and
returns a `RuleResult`. Rules are grouped by `doc_type`. The scoring
engine in `quality.py` runs the universal rules plus the per-type rules
for each indexed doc.

Hard rules → red when violated.
Soft rules → amber when violated (only matters if all hard rules pass).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loader import RuleDoc

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


SEVERITY_HARD = "hard"
SEVERITY_SOFT = "soft"


@dataclass
class RuleResult:
    rule_id: str
    severity: str  # "hard" | "soft"
    passed: bool
    message: str  # human-readable description (passed or fix-it)


@dataclass
class RuleContext:
    """Context passed to every rule. Project root is needed for filesystem
    checks (executable bit on gate scripts, INDEX freshness)."""

    project_root: Path
    project_files: list[RuleDoc]
    on_disk_index: str | None  # raw INDEX.md content from disk, or None


# A rule is a callable: (doc, ctx) -> RuleResult.
Rule = Callable[[RuleDoc, RuleContext], RuleResult]


# ---------------------------------------------------------------------------
# Layout / required-files constants (mirror scripts/validate-rules.py)
# ---------------------------------------------------------------------------

REQUIRED_FILES = (
    "AGENTS.md",
    "core/guardrails.md",
    "core/definition-of-done.md",
    "core/glossary.md",
    "architecture/overview.md",
    "gates/README.md",
)
REQUIRED_WORKFLOWS = ("new-feature", "bug-fix", "security-fix", "refactor")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FENCED_CODE_RE = re.compile(r"^```", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
_TASK_CHECKBOX_RE = re.compile(r"^\s*-\s*\[\s\]\s+", re.MULTILINE)
_NUMBERED_STEP_RE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+\S", re.MULTILINE)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _h2_titles(text: str) -> list[str]:
    return [m.group(1).strip() for m in _H2_RE.finditer(text)]


def _has_h2(text: str, *needles: str) -> bool:
    titles = " | ".join(_h2_titles(text)).lower()
    return all(n.lower() in titles for n in needles)


def _ok(rule_id: str, severity: str, message: str) -> RuleResult:
    return RuleResult(rule_id=rule_id, severity=severity, passed=True, message=message)


def _fail(rule_id: str, severity: str, message: str) -> RuleResult:
    return RuleResult(rule_id=rule_id, severity=severity, passed=False, message=message)


def _check(rule_id: str, severity: str, condition: bool, message: str) -> RuleResult:
    return RuleResult(rule_id=rule_id, severity=severity, passed=bool(condition), message=message)


# ---------------------------------------------------------------------------
# Universal rules - run against every indexed doc
# ---------------------------------------------------------------------------


def _rule_has_h1(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    return _check(
        "universal.has_h1",
        SEVERITY_HARD,
        bool(_H1_RE.search(doc.content)),
        "Has an H1 (`# Title`) line",
    )


def _rule_non_empty(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    return _check(
        "universal.non_empty",
        SEVERITY_HARD,
        bool(doc.content.strip()),
        "Body is non-empty after frontmatter is stripped",
    )


def _rule_known_doc_type(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    return _check(
        "universal.recognized_path",
        SEVERITY_HARD,
        doc.doc_type != "other",
        "Path matches the per-project layout (not classified as 'other')",
    )


# INDEX.md is auto-generated and intentionally has no frontmatter.
_FRONTMATTER_EXEMPT_TYPES = {"index"}


def _rule_has_title(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    if doc.doc_type in _FRONTMATTER_EXEMPT_TYPES:
        return _ok(
            "universal.frontmatter_title",
            SEVERITY_SOFT,
            "Auto-generated doc - frontmatter not required",
        )
    title = doc.metadata.get("title")
    return _check(
        "universal.frontmatter_title",
        SEVERITY_SOFT,
        isinstance(title, str) and bool(title.strip()),
        "Frontmatter has a `title:` (powers BM25 search and INDEX entries)",
    )


def _rule_has_description(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    if doc.doc_type in _FRONTMATTER_EXEMPT_TYPES:
        return _ok(
            "universal.frontmatter_description",
            SEVERITY_SOFT,
            "Auto-generated doc - frontmatter not required",
        )
    desc = doc.metadata.get("description")
    return _check(
        "universal.frontmatter_description",
        SEVERITY_SOFT,
        isinstance(desc, str) and bool(desc.strip()),
        "Frontmatter has a `description:` (shown in `playbook_find` and INDEX)",
    )


def _rule_word_count(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    wc = _word_count(doc.content)
    return _check(
        "universal.word_count",
        SEVERITY_SOFT,
        wc >= 80,
        f"Body word count ≥ 80 (currently {wc})",
    )


UNIVERSAL_RULES: tuple[Rule, ...] = (
    _rule_known_doc_type,
    _rule_non_empty,
    _rule_has_h1,
    _rule_has_title,
    _rule_has_description,
    _rule_word_count,
)


# ---------------------------------------------------------------------------
# AGENTS.md
# ---------------------------------------------------------------------------


def _rule_agents_mentions_entry_points(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    text = doc.content
    # Substring match accepts every historical spelling of the entry point
    # (`start_task`, `playbook_start_task`, `playbook_start`).
    has = (
        "playbook_start" in text
        or "start_task" in text
        or "INDEX.md" in text
        or "core/guardrails.md" in text
    )
    return _check(
        "agents.mentions_entry_points",
        SEVERITY_HARD,
        has,
        "Mentions `playbook_start`, `INDEX.md`, or `core/guardrails.md` "
        "(so agents know how to enter)",
    )


def _rule_agents_context_table(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    needles = (
        "core/",
        "architecture/",
        "languages/",
        "patterns/",
        "skills/",
        "workflows/",
        "gates/",
    )
    found = sum(1 for n in needles if n in doc.content)
    return _check(
        "agents.context_table",
        SEVERITY_SOFT,
        found >= 5,
        f"Lists ≥ 5 of the standard layout folders in a CONTEXT DOCS section (currently {found})",
    )


def _rule_agents_word_count(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    wc = _word_count(doc.content)
    return _check(
        "agents.word_count",
        SEVERITY_SOFT,
        wc >= 250,
        f"AGENTS.md should be ≥ 250 words (currently {wc})",
    )


AGENTS_RULES: tuple[Rule, ...] = (
    _rule_agents_mentions_entry_points,
    _rule_agents_context_table,
    _rule_agents_word_count,
)


# ---------------------------------------------------------------------------
# INDEX.md
# ---------------------------------------------------------------------------


_INDEX_AUTOGEN_MARKER = "AUTO-GENERATED by scripts/validate-rules.py"


def _rule_index_autogen_header(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    first = next((line for line in doc.content.splitlines() if line.strip()), "")
    return _check(
        "index.autogen_header",
        SEVERITY_HARD,
        _INDEX_AUTOGEN_MARKER in first,
        "First non-blank line is the auto-generated header marker",
    )


def _rule_index_up_to_date(doc: RuleDoc, ctx: RuleContext) -> RuleResult:
    # Lazy import to avoid a circular import (index_render imports from loader,
    # not from quality_rules, so this is safe).
    from index_render import render_index

    if ctx.on_disk_index is None:
        return _fail(
            "index.up_to_date",
            SEVERITY_HARD,
            "INDEX.md not present on disk - run `python scripts/validate-rules.py --regen-index`",
        )

    rendered = render_index(_project_name(ctx), ctx.project_files)
    return _check(
        "index.up_to_date",
        SEVERITY_HARD,
        ctx.on_disk_index == rendered,
        "INDEX.md matches what the generator would emit (no drift)",
    )


def _rule_index_has_all_groups(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    needles = ("## Workflows", "## Skills", "## Patterns", "## Languages", "## Gates")
    missing = [n for n in needles if n not in doc.content]
    return _check(
        "index.has_all_groups",
        SEVERITY_SOFT,
        not missing,
        "Contains Workflows / Skills / Patterns / Languages / Gates sections"
        + (f" (missing: {', '.join(missing)})" if missing else ""),
    )


INDEX_RULES: tuple[Rule, ...] = (
    _rule_index_autogen_header,
    _rule_index_up_to_date,
    _rule_index_has_all_groups,
)


# ---------------------------------------------------------------------------
# core/guardrails.md
# ---------------------------------------------------------------------------


def _rule_guardrails_must_and_must_not(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    has_must = bool(re.search(r"^##\s+MUST\b", doc.content, re.MULTILINE))
    has_must_not = bool(re.search(r"^##\s+MUST NOT\b", doc.content, re.MULTILINE))
    return _check(
        "guardrails.must_and_must_not",
        SEVERITY_HARD,
        has_must and has_must_not,
        "Has both `## MUST` and `## MUST NOT` sections",
    )


def _rule_guardrails_covers_essentials(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    text = doc.content.lower()
    secrets = "secret" in text or "credential" in text or "password" in text
    scope = "scope" in text or "honesty" in text or "ask" in text
    return _check(
        "guardrails.covers_essentials",
        SEVERITY_SOFT,
        secrets and scope,
        "Mentions secrets/credentials AND scope/honesty",
    )


def _rule_guardrails_links_gate(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    return _check(
        "guardrails.links_gate",
        SEVERITY_SOFT,
        "gates/scripts/verify-" in doc.content,
        "References `gates/scripts/verify-*` so authors know how to close out a task",
    )


GUARDRAILS_RULES: tuple[Rule, ...] = (
    _rule_guardrails_must_and_must_not,
    _rule_guardrails_covers_essentials,
    _rule_guardrails_links_gate,
)


# ---------------------------------------------------------------------------
# core/definition-of-done.md
# ---------------------------------------------------------------------------


def _rule_dod_has_checkbox(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    return _check(
        "dod.has_checkbox",
        SEVERITY_HARD,
        bool(_TASK_CHECKBOX_RE.search(doc.content)),
        "Has at least one Markdown task checkbox (`- [ ]`)",
    )


def _rule_dod_references_gate(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    return _check(
        "dod.references_gate",
        SEVERITY_SOFT,
        "gates/scripts/verify-" in doc.content,
        "References `gates/scripts/verify-*.sh`",
    )


def _rule_dod_three_sections(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    n = len(_h2_titles(doc.content))
    return _check(
        "dod.three_sections",
        SEVERITY_SOFT,
        n >= 3,
        f"Has ≥ 3 H2 sections (currently {n})",
    )


DOD_RULES: tuple[Rule, ...] = (
    _rule_dod_has_checkbox,
    _rule_dod_references_gate,
    _rule_dod_three_sections,
)


# ---------------------------------------------------------------------------
# core/glossary.md
# ---------------------------------------------------------------------------


def _rule_glossary_has_terms(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    table_rows = len(_TABLE_ROW_RE.findall(doc.content))
    bullets = len(_BULLET_RE.findall(doc.content))
    return _check(
        "glossary.has_terms",
        SEVERITY_HARD,
        table_rows >= 5 or bullets >= 5,
        f"Has a Markdown table OR ≥ 5 bullets (rows={table_rows}, bullets={bullets})",
    )


def _rule_glossary_substantial(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    table_rows = len(_TABLE_ROW_RE.findall(doc.content))
    bullets = len(_BULLET_RE.findall(doc.content))
    n = max(table_rows, bullets)
    return _check(
        "glossary.substantial",
        SEVERITY_SOFT,
        n >= 10,
        f"Has ≥ 10 terms (currently {n})",
    )


GLOSSARY_RULES: tuple[Rule, ...] = (
    _rule_glossary_has_terms,
    _rule_glossary_substantial,
)


# ---------------------------------------------------------------------------
# architecture/overview.md
# ---------------------------------------------------------------------------


def _rule_arch_word_count(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    wc = _word_count(doc.content)
    return _check(
        "architecture.word_count",
        SEVERITY_HARD,
        wc >= 150,
        f"Architecture overview ≥ 150 words (currently {wc})",
    )


def _rule_arch_has_table_or_code(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    has = bool(_TABLE_ROW_RE.search(doc.content)) or bool(_FENCED_CODE_RE.search(doc.content))
    return _check(
        "architecture.has_table_or_code",
        SEVERITY_SOFT,
        has,
        "Has a Markdown table or fenced code block (module map / tech stack / topology)",
    )


def _rule_arch_covers_topics(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    titles = " ".join(_h2_titles(doc.content)).lower()
    has_overview = "overview" in titles
    has_stack = "stack" in titles
    has_modules = "module" in titles or "boundar" in titles or "topology" in titles
    return _check(
        "architecture.covers_topics",
        SEVERITY_SOFT,
        has_overview and has_stack and has_modules,
        "H2 sections cover overview, stack, and modules/boundaries",
    )


ARCHITECTURE_RULES: tuple[Rule, ...] = (
    _rule_arch_word_count,
    _rule_arch_has_table_or_code,
    _rule_arch_covers_topics,
)


# ---------------------------------------------------------------------------
# architecture/decisions/<n>.md (ADRs)
# ---------------------------------------------------------------------------


_ADR_FILENAME_RE = re.compile(r"^\d{4}-[a-z0-9-]+$")


def _rule_adr_filename(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    stem = doc.relative_path.rsplit("/", 1)[-1]
    if stem.endswith(".md"):
        stem = stem[:-3]
    return _check(
        "adr.filename",
        SEVERITY_HARD,
        bool(_ADR_FILENAME_RE.match(stem)),
        f"Filename matches `NNNN-slug` (currently `{stem}`)",
    )


def _rule_adr_sections(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    titles = " ".join(_h2_titles(doc.content)).lower()
    needles = ("status", "context", "decision", "consequences")
    missing = [n for n in needles if n not in titles]
    return _check(
        "adr.sections",
        SEVERITY_SOFT,
        not missing,
        "ADR has Status / Context / Decision / Consequences sections"
        + (f" (missing: {', '.join(missing)})" if missing else ""),
    )


ADR_RULES: tuple[Rule, ...] = (
    _rule_adr_filename,
    _rule_adr_sections,
)


# ---------------------------------------------------------------------------
# languages/<lang>/*.md
# ---------------------------------------------------------------------------


def _rule_language_meta_set(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    lang = doc.metadata.get("language")
    return _check(
        "language.metadata_set",
        SEVERITY_HARD,
        isinstance(lang, str) and bool(lang.strip()),
        "Frontmatter `language:` is set (loader auto-injects from path; absence indicates a layout bypass)",
    )


def _rule_language_three_sections(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    n = len(_h2_titles(doc.content))
    return _check(
        "language.three_sections",
        SEVERITY_SOFT,
        n >= 3,
        f"Standards doc has ≥ 3 H2 sections (currently {n})",
    )


def _rule_language_naming_and_extras(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    lc = doc.content.lower()
    has_naming = "naming" in lc
    has_other = "logging" in lc or "depend" in lc
    return _check(
        "language.naming_and_extras",
        SEVERITY_SOFT,
        has_naming and has_other,
        "Mentions naming AND at least one of logging/dependencies",
    )


_FRAMEWORK_NEEDLES = (
    "junit",
    "pytest",
    "vitest",
    "jest",
    "rspec",
    "rest assured",
    "restassured",
    "react testing library",
    "rtl",
    "testcontainers",
    "mockito",
    "wiremock",
    "cypress",
    "playwright",
    "shellcheck",
    "yamllint",
)


def _rule_language_framework(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    lc = doc.content.lower()
    found = [n for n in _FRAMEWORK_NEEDLES if n in lc]
    return _check(
        "language.testing_framework",
        SEVERITY_SOFT,
        bool(found),
        "Names a concrete testing framework" + (f" ({', '.join(found[:3])})" if found else ""),
    )


def _rule_language_has_code_block(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    return _check(
        "language.has_code_block",
        SEVERITY_SOFT,
        bool(_FENCED_CODE_RE.search(doc.content)),
        "Has at least one fenced code block",
    )


_ANTIPATTERN_BULLET_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:❌|DO NOT\b|❗\s*DON'T\b)",
    re.MULTILINE,
)


def _rule_language_antipattern_count(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    n = len(_ANTIPATTERN_BULLET_RE.findall(doc.content))
    return _check(
        "language.antipattern_count",
        SEVERITY_HARD,
        n >= 5,
        f"Has ≥ 5 ❌ / 'DO NOT' bullets (currently {n})",
    )


def _rule_language_antipattern_substantial(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    n = len(_ANTIPATTERN_BULLET_RE.findall(doc.content))
    return _check(
        "language.antipattern_substantial",
        SEVERITY_SOFT,
        n >= 10,
        f"Has ≥ 10 anti-pattern bullets (currently {n})",
    )


# Different language docs run different subsets:
LANGUAGE_STANDARDS_RULES: tuple[Rule, ...] = (
    _rule_language_meta_set,
    _rule_language_three_sections,
    _rule_language_naming_and_extras,
)
LANGUAGE_TESTING_RULES: tuple[Rule, ...] = (
    _rule_language_meta_set,
    _rule_language_framework,
    _rule_language_has_code_block,
)
LANGUAGE_ANTIPATTERNS_RULES: tuple[Rule, ...] = (
    _rule_language_meta_set,
    _rule_language_antipattern_count,
    _rule_language_antipattern_substantial,
)


# ---------------------------------------------------------------------------
# patterns/<name>.md
# ---------------------------------------------------------------------------


def _rule_pattern_has_code_block(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    return _check(
        "pattern.has_code_block",
        SEVERITY_HARD,
        bool(_FENCED_CODE_RE.search(doc.content)),
        "Pattern has at least one fenced code block (canonical example)",
    )


def _rule_pattern_use_when(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    has_use_when = re.search(r"\buse this when\b|\bwhen to use\b", doc.content, re.IGNORECASE)
    triggers = doc.metadata.get("triggers")
    has_triggers = isinstance(triggers, list) and bool(triggers)
    return _check(
        "pattern.use_when",
        SEVERITY_SOFT,
        bool(has_use_when) or has_triggers,
        "Has a 'Use this when' line OR `triggers:` frontmatter",
    )


def _rule_pattern_see_also(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    see_also = doc.metadata.get("see_also")
    return _check(
        "pattern.see_also",
        SEVERITY_SOFT,
        isinstance(see_also, list) and bool(see_also),
        "Frontmatter `see_also:` is set (drives Next Calls in tool responses)",
    )


PATTERN_RULES: tuple[Rule, ...] = (
    _rule_pattern_has_code_block,
    _rule_pattern_use_when,
    _rule_pattern_see_also,
)


# ---------------------------------------------------------------------------
# skills/<name>.md
# ---------------------------------------------------------------------------


def _rule_skill_steps(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    n = len(_NUMBERED_STEP_RE.findall(doc.content))
    return _check(
        "skill.numbered_steps",
        SEVERITY_HARD,
        n >= 3,
        f"Has a numbered step list with ≥ 3 items (currently {n})",
    )


def _rule_skill_triggers(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    triggers = doc.metadata.get("triggers")
    return _check(
        "skill.triggers",
        SEVERITY_SOFT,
        isinstance(triggers, list) and bool(triggers),
        "Frontmatter `triggers:` is set (otherwise `playbook_start` won't surface this skill)",
    )


def _rule_skill_see_also(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    see_also = doc.metadata.get("see_also")
    return _check(
        "skill.see_also",
        SEVERITY_SOFT,
        isinstance(see_also, list) and bool(see_also),
        "Frontmatter `see_also:` is set",
    )


SKILL_RULES: tuple[Rule, ...] = (
    _rule_skill_steps,
    _rule_skill_triggers,
    _rule_skill_see_also,
)


# ---------------------------------------------------------------------------
# workflows/<name>.md
# ---------------------------------------------------------------------------


def _rule_workflow_triggers(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    triggers = doc.metadata.get("triggers")
    return _check(
        "workflow.triggers",
        SEVERITY_HARD,
        isinstance(triggers, list) and bool(triggers),
        "Frontmatter `triggers:` is set (required for `playbook_start` matching)",
    )


def _rule_workflow_steps(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    has_steps_h2 = bool(re.search(r"^##\s+Steps\b", doc.content, re.MULTILINE | re.IGNORECASE))
    n_steps = len(_NUMBERED_STEP_RE.findall(doc.content))
    return _check(
        "workflow.steps",
        SEVERITY_HARD,
        has_steps_h2 and n_steps >= 3,
        f"Has a `## Steps` section with ≥ 3 numbered items (steps={n_steps}, has_steps_h2={has_steps_h2})",
    )


def _rule_workflow_gates(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    gates = doc.metadata.get("gates")
    return _check(
        "workflow.gates",
        SEVERITY_SOFT,
        isinstance(gates, list) and bool(gates),
        "Frontmatter `gates:` names the verify script that closes this workflow",
    )


def _rule_workflow_see_also(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    see_also = doc.metadata.get("see_also") or []
    if not isinstance(see_also, list):
        see_also = []
    relevant = [
        s
        for s in see_also
        if isinstance(s, str) and (s.startswith("skill:") or s.startswith("pattern:"))
    ]
    return _check(
        "workflow.see_also",
        SEVERITY_SOFT,
        bool(relevant),
        "Frontmatter `see_also:` references at least one skill or pattern",
    )


_STANDARD_WORKFLOW_NAMES = set(REQUIRED_WORKFLOWS)


def _rule_workflow_standard_name(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    name = doc.name
    return _check(
        "workflow.standard_name",
        SEVERITY_SOFT,
        name in _STANDARD_WORKFLOW_NAMES,
        f"Workflow name is one of {sorted(_STANDARD_WORKFLOW_NAMES)} (currently `{name}`)",
    )


WORKFLOW_RULES: tuple[Rule, ...] = (
    _rule_workflow_triggers,
    _rule_workflow_steps,
    _rule_workflow_gates,
    _rule_workflow_see_also,
    _rule_workflow_standard_name,
)


# ---------------------------------------------------------------------------
# gates/README.md
# ---------------------------------------------------------------------------


def _rule_gate_lists_existing_script(doc: RuleDoc, ctx: RuleContext) -> RuleResult:
    scripts = doc.metadata.get("gate_scripts") or []
    if not scripts:
        return _fail(
            "gate.has_script",
            SEVERITY_HARD,
            "No verify-*.sh scripts under gates/scripts/",
        )
    scripts_dir = ctx.project_root / "gates" / "scripts"
    real = [s for s in scripts if (scripts_dir / s).is_file()]
    return _check(
        "gate.has_script",
        SEVERITY_HARD,
        bool(real),
        f"At least one verify-*.sh exists under gates/scripts/ ({', '.join(real) or 'none'})",
    )


def _rule_gate_scripts_executable(doc: RuleDoc, ctx: RuleContext) -> RuleResult:
    scripts_dir = ctx.project_root / "gates" / "scripts"
    if not scripts_dir.is_dir():
        return _fail(
            "gate.scripts_executable",
            SEVERITY_HARD,
            "gates/scripts/ directory not found",
        )
    bad: list[str] = []
    for s in scripts_dir.iterdir():
        if s.is_file() and s.suffix == ".sh" and not os.access(s, os.X_OK):
            bad.append(s.name)
    return _check(
        "gate.scripts_executable",
        SEVERITY_HARD,
        not bad,
        "All gates/scripts/*.sh have the executable bit"
        + (f" (missing chmod +x on: {', '.join(bad)})" if bad else ""),
    )


def _rule_gate_documents_each_script(doc: RuleDoc, _ctx: RuleContext) -> RuleResult:
    scripts = doc.metadata.get("gate_scripts") or []
    missing = [s for s in scripts if s not in doc.content]
    return _check(
        "gate.documents_each_script",
        SEVERITY_SOFT,
        not missing,
        "README mentions every gates/scripts/*.sh by name"
        + (f" (missing: {', '.join(missing)})" if missing else ""),
    )


GATE_RULES: tuple[Rule, ...] = (
    _rule_gate_lists_existing_script,
    _rule_gate_scripts_executable,
    _rule_gate_documents_each_script,
)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _project_name(ctx: RuleContext) -> str:
    return ctx.project_root.name


def rules_for(doc: RuleDoc) -> tuple[Rule, ...]:
    """Return the per-type rule set for this doc (excluding universal rules)."""
    if doc.doc_type == "agents":
        return AGENTS_RULES
    if doc.doc_type == "index":
        return INDEX_RULES
    if doc.doc_type == "guardrails":
        return GUARDRAILS_RULES
    if doc.doc_type == "definition-of-done":
        return DOD_RULES
    if doc.doc_type == "glossary":
        return GLOSSARY_RULES
    if doc.doc_type == "architecture":
        return ARCHITECTURE_RULES
    if doc.doc_type == "architecture-decision":
        return ADR_RULES
    if doc.doc_type == "language-rules":
        # languages/<lang>/<doc>.md - pick subset by `<doc>` part of name.
        sub = doc.name.split("/", 1)[1] if "/" in doc.name else doc.name
        if sub == "standards":
            return LANGUAGE_STANDARDS_RULES
        if sub == "testing":
            return LANGUAGE_TESTING_RULES
        if sub == "anti-patterns":
            return LANGUAGE_ANTIPATTERNS_RULES
        return LANGUAGE_STANDARDS_RULES  # safe default
    if doc.doc_type == "pattern":
        return PATTERN_RULES
    if doc.doc_type == "skill":
        return SKILL_RULES
    if doc.doc_type == "workflow":
        return WORKFLOW_RULES
    if doc.doc_type == "gate":
        return GATE_RULES
    return ()
