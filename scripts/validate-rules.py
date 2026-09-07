#!/usr/bin/env python3
"""
validate-rules.py - Pre-commit / CI gate for standards and requirements docs.

Modes:
    python scripts/validate-rules.py --corpus standards --check
    python scripts/validate-rules.py --corpus requirements --check
    python scripts/validate-rules.py --corpus all --check          # default
    python scripts/validate-rules.py --corpus all --regen-index

Exits non-zero on any hard error (draft requirements never hard-fail CI - 
see requirement_rules status-aware severity; this script reports them as
warnings when --corpus requirements and all docs are draft).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


# ANSI helpers (disabled when not a TTY)
def _c(code: str, text: str) -> str:
    if not sys.stderr.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _red(t: str) -> str: return _c("31", t)
def _yellow(t: str) -> str: return _c("33", t)
def _bold(t: str) -> str: return _c("1", t)
def _dim(t: str) -> str: return _c("2", t)

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"

# Reuse the loader so this script always agrees with what the server sees.
sys.path.insert(0, str(MCP_DIR))
from corpus import (  # noqa: E402
    REQUIREMENTS,
    STANDARDS,
    requirements_spec,
    standards_spec,
)
from loader import (  # noqa: E402
    SEE_ALSO_KINDS,
    SEE_ALSO_TOOLS,
    RuleDoc,
    _parse_frontmatter,
    parse_corpus,
)
from index_render import render_index  # noqa: E402
# The corpus expectations live with the rule library, not here - one definition
# behind both this CI gate and the dashboard's quality pages.
from quality_rules import REQUIRED_FILES, REQUIRED_WORKFLOWS  # noqa: E402
from refs import RefError, parse_ref  # noqa: E402

LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

REQUIRED_REQ_WORKFLOWS = ("write-prd", "write-story")


_FIX_HINTS: dict[str, str] = {
    "missing required file": "create the file (see playbook docs for required content)",
    "expected at least one languages": "add languages/<lang>/standards.md for each stack",
    "missing workflows": "add a workflow doc under workflows/<name>.md",
    "not executable": "run: chmod +x <file>",
    "broken link": "update the link target so it points to an existing file",
    "does not match the expected layout": "rename or move the file to match the playbook layout",
    "starts with '---' but no closing": "add a closing '---' line after the YAML frontmatter",
    "out of date": "run: python scripts/validate-rules.py --regen-index",
}


def _hint(msg: str) -> str:
    for key, hint in _FIX_HINTS.items():
        if key in msg:
            return hint
    return ""


# (project, category, message) tuples - richer than bare strings
_Error = tuple[str, str, str]   # (project, category, detail)


# ---------------------------------------------------------------------------
# Project discovery
# ---------------------------------------------------------------------------


def _project_dirs(corpus_root: Path) -> list[Path]:
    if not corpus_root.is_dir():
        return []
    return [
        p
        for p in sorted(corpus_root.iterdir())
        if p.is_dir() and not p.name.startswith(".")
    ]


def _corpus_root(name: str) -> Path:
    if name == "standards":
        return standards_spec().root
    if name == "requirements":
        return requirements_spec().root
    raise ValueError(name)


# ---------------------------------------------------------------------------
# Structure checks - standards
# ---------------------------------------------------------------------------


def _check_required_files(projects: list[Path]) -> list[_Error]:
    errors: list[_Error] = []
    for project in projects:
        for rel in REQUIRED_FILES:
            if not (project / rel).is_file():
                errors.append((project.name, "required files", f"missing required file {rel}"))
        langs_dir = project / "languages"
        has_standards = False
        if langs_dir.is_dir():
            for lang_dir in langs_dir.iterdir():
                if (lang_dir / "standards.md").is_file():
                    has_standards = True
                    break
        if not has_standards:
            errors.append((
                project.name, "required files",
                "expected at least one languages/<lang>/standards.md",
            ))
        for wf in REQUIRED_WORKFLOWS:
            if not (project / "workflows" / f"{wf}.md").is_file():
                errors.append((project.name, "workflows", f"missing workflows/{wf}.md"))
        scripts_dir = project / "gates" / "scripts"
        if scripts_dir.is_dir():
            for s in scripts_dir.iterdir():
                if s.is_file() and s.suffix == ".sh" and not os.access(s, os.X_OK):
                    errors.append((
                        project.name, "gate scripts",
                        f"gates/scripts/{s.name} is not executable (chmod +x)",
                    ))
    return errors


def _check_req_structure(projects: list[Path]) -> list[_Error]:
    """Soft structure for requirements projects (AGENTS + write workflows)."""
    errors: list[_Error] = []
    for project in projects:
        if not (project / "AGENTS.md").is_file():
            errors.append((project.name, "required files", "missing required file AGENTS.md"))
        for wf in REQUIRED_REQ_WORKFLOWS:
            if not (project / "workflows" / f"{wf}.md").is_file():
                errors.append(
                    (project.name, "workflows", f"missing workflows/{wf}.md")
                )
    return errors


def _check_frontmatter(corpus_root: Path) -> list[_Error]:
    errors: list[_Error] = []
    if not corpus_root.is_dir():
        return errors
    for md in corpus_root.rglob("*.md"):
        rel = md.relative_to(corpus_root)
        if len(rel.parts) < 2:
            continue
        project = rel.parts[0]
        try:
            content = md.read_text(encoding="utf-8")
        except OSError as e:
            errors.append((project, "frontmatter", f"{rel}: cannot read ({e})"))
            continue
        if content.startswith("---"):
            metadata, _ = _parse_frontmatter(content)
            if not metadata and "---" not in content[3:]:
                errors.append((
                    project, "frontmatter",
                    f"{rel}: starts with '---' but no closing '---' boundary",
                ))
    return errors


def _check_links(corpus_root: Path) -> list[_Error]:
    errors: list[_Error] = []
    if not corpus_root.is_dir():
        return errors
    for md in corpus_root.rglob("*.md"):
        rel = md.relative_to(corpus_root)
        if len(rel.parts) < 2:
            continue
        project = rel.parts[0]
        try:
            content = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for _, target in LINK_RE.findall(content):
            if (
                target.startswith(("http://", "https://", "mailto:", "#"))
                or target.startswith("<")
            ):
                continue
            path_part = target.split("#", 1)[0].split("?", 1)[0]
            if not path_part:
                continue
            resolved = (md.parent / path_part).resolve()
            if not resolved.exists():
                errors.append((project, "broken links", f"{rel}: broken link -> {target}"))
    return errors


def _check_no_other_doc_types(docs: list[RuleDoc]) -> list[_Error]:
    return [
        (d.project, "layout", f"{d.project}/{d.relative_path}: does not match the expected layout")
        for d in docs
        if d.doc_type == "other"
    ]


def _check_see_also_kinds(docs: list[RuleDoc]) -> list[_Error]:
    """Reject `see_also` / `targets` entries the server cannot render.

    Entries are validated with tools/refs.py - the same parser playbook_get
    uses - so a doc that passes here is guaranteed to render a live Next Call
    rather than silently dropping the bullet.
    """
    errors: list[_Error] = []
    for d in docs:
        for key in ("see_also", "targets"):
            raw = d.metadata.get(key) or []
            if not raw:
                continue
            if not isinstance(raw, list):
                errors.append(
                    (d.project, key, f"{d.project}/{d.relative_path}: {key} must be a list")
                )
                continue
            for entry in raw:
                where = f"{d.project}/{d.relative_path}: {key} entry '{entry}'"
                if not isinstance(entry, str) or ":" not in entry:
                    errors.append((d.project, key, f"{where} is not '<kind>:<name>'"))
                    continue
                kind, _, name = entry.partition(":")
                kind, name = kind.strip(), name.strip()
                if kind == "tool":
                    if name not in SEE_ALSO_TOOLS:
                        errors.append((
                            d.project, key,
                            f"{where} names unknown tool '{name}' "
                            f"(expected one of {', '.join(SEE_ALSO_TOOLS)})",
                        ))
                    continue
                if kind not in SEE_ALSO_KINDS:
                    errors.append((
                        d.project, key,
                        f"{where} has unknown kind '{kind}' "
                        f"(expected one of {', '.join(SEE_ALSO_KINDS)})",
                    ))
                    continue
                try:
                    parse_ref(entry)
                except RefError as exc:
                    errors.append((d.project, key, f"{where} is not a valid ref: {exc}"))
    return errors


# ---------------------------------------------------------------------------
# INDEX.md generator
# ---------------------------------------------------------------------------


def _regen_index(
    projects: list[Path], all_docs: list[RuleDoc], *, check_only: bool = False
) -> list[_Error]:
    """Write INDEX.md (or, in check mode, return diff errors)."""
    errors: list[_Error] = []
    by_project: dict[str, list[RuleDoc]] = {}
    for d in all_docs:
        if d.doc_type == "other":
            continue
        if d.relative_path == "INDEX.md":
            continue
        by_project.setdefault(d.project, []).append(d)

    for p in projects:
        target = p / "INDEX.md"
        rendered = render_index(p.name, by_project.get(p.name, []))
        if check_only:
            existing = target.read_text(encoding="utf-8") if target.is_file() else ""
            if existing != rendered:
                errors.append((
                    p.name, "index",
                    f"{p.name}/INDEX.md is out of date - run "
                    "`python scripts/validate-rules.py --regen-index`.",
                ))
        else:
            target.write_text(rendered, encoding="utf-8")
    return errors


# ---------------------------------------------------------------------------
# Requirements quality (status-aware) - imported lazily when available
# ---------------------------------------------------------------------------


def _check_requirement_docs(
    docs: list[RuleDoc], standards_docs: list[RuleDoc]
) -> tuple[list[_Error], list[_Error]]:
    """Return (hard_errors, soft_warnings) using requirement_rules."""
    try:
        from requirement_rules import validate_requirement_docs
    except ImportError:
        return [], []
    return validate_requirement_docs(docs, standards_docs)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        choices=("standards", "requirements", "all"),
        default="all",
        help="Which corpus to validate (default: all).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--regen-index",
        action="store_true",
        help="Regenerate <project>/INDEX.md files (writes to disk).",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="Validate AND fail if any INDEX.md is out of date.",
    )
    return parser.parse_args(argv)


def _validate_standards(*, check: bool, regen: bool) -> list[_Error]:
    root = _corpus_root("standards")
    print(f"Validating standards under {root}")
    projects = _project_dirs(root)
    if not projects:
        print("ERROR: no standards project directories found.", file=sys.stderr)
        return [(".", "projects", "no standards project directories found")]

    docs = parse_corpus(standards_spec())
    print(f"  Loaded {len(docs)} standards docs across {len(projects)} project(s).")

    if regen:
        _regen_index(projects, docs, check_only=False)
        print(f"  Regenerated INDEX.md for {len(projects)} project(s).")
        return []

    errors: list[_Error] = []
    errors += _check_required_files(projects)
    errors += _check_frontmatter(root)
    errors += _check_links(root)
    errors += _check_no_other_doc_types(docs)
    errors += _check_see_also_kinds(docs)
    if check:
        errors += _regen_index(projects, docs, check_only=True)
    return errors


def _validate_requirements(*, check: bool, regen: bool) -> tuple[list[_Error], list[_Error]]:
    root = _corpus_root("requirements")
    print(f"Validating requirements under {root}")
    projects = _project_dirs(root)
    if not projects:
        print("  No requirements projects yet - OK.")
        return [], []

    docs = parse_corpus(requirements_spec())
    print(f"  Loaded {len(docs)} requirements docs across {len(projects)} project(s).")

    if regen:
        _regen_index(projects, docs, check_only=False)
        print(f"  Regenerated INDEX.md for {len(projects)} project(s).")
        return [], []

    errors: list[_Error] = []
    warnings: list[_Error] = []
    errors += _check_req_structure(projects)
    errors += _check_frontmatter(root)
    errors += _check_links(root)
    errors += _check_no_other_doc_types(docs)
    errors += _check_see_also_kinds(docs)

    standards_docs = parse_corpus(standards_spec()) if STANDARDS.root.is_dir() else []
    hard, soft = _check_requirement_docs(docs, standards_docs)
    errors += hard
    warnings += soft

    if check:
        errors += _regen_index(projects, docs, check_only=True)
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    corpora = (
        ("standards", "requirements")
        if args.corpus == "all"
        else (args.corpus,)
    )

    all_errors: list[_Error] = []
    all_warnings: list[_Error] = []

    for name in corpora:
        if name == "standards":
            all_errors += _validate_standards(check=args.check, regen=args.regen_index)
        else:
            errs, warns = _validate_requirements(check=args.check, regen=args.regen_index)
            all_errors += errs
            all_warnings += warns

    if args.regen_index:
        return 0

    if all_warnings:
        _print_errors(all_warnings, label="WARNINGS", color=_yellow)

    if all_errors:
        _print_errors(all_errors, label="FAILED", color=_red)
        return 1
    print("OK")
    return 0


def _print_errors(
    errors: list[_Error],
    *,
    label: str = "FAILED",
    color=_red,
) -> None:
    total = len(errors)
    print(
        f"\n{_bold(color(label + ':'))} {_bold(str(total))} issue(s) across "
        f"{len({e[0] for e in errors})} project(s):\n",
        file=sys.stderr,
    )

    by_project: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for project, category, msg in errors:
        by_project[project][category].append(msg)

    for project, categories in sorted(by_project.items()):
        proj_count = sum(len(v) for v in categories.values())
        print(
            f"  {_bold(_yellow(project))}  "
            f"{_dim(f'({proj_count} issue(s))')}",
            file=sys.stderr,
        )
        for category, msgs in sorted(categories.items()):
            print(f"    {_bold(category)}", file=sys.stderr)
            for msg in msgs:
                hint = _hint(msg)
                print(f"      {_red('✗')} {msg}", file=sys.stderr)
                if hint:
                    print(f"        {_dim('→ ' + hint)}", file=sys.stderr)
        print(file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
