#!/usr/bin/env python3
"""Deterministic setup steps for /dev-playbook-init.

The command drives the interactive parts (checking the server, scaffolding a
project); this helper does the file/config changes that must be exact and
idempotent - registering the MCP server at user scope, editing the global
CLAUDE.md, and dropping the marker files the hooks read. Stdlib only, so it runs
under whatever python3 the host has, with or without a repo checkout.

Usage:
  dp_init.py [--server-url URL] [--token TOK] [--no-user-scope]   # configure
  dp_init.py --configured PROJECT                                 # mark a repo done
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import playbook_db  # noqa: E402  (after sys.path shim, by design)

DEFAULT_URL = "http://localhost:8420/sse"
CLAUDE_MD = Path.home() / ".claude" / "CLAUDE.md"
START = "<!-- dev-playbook:start -->"
END = "<!-- dev-playbook:end -->"


def _block() -> str:
    return "\n".join(
        [
            START,
            "## dev-playbook standards (managed by /dev-playbook-init)",
            "",
            "For any repo that has a dev-playbook standards project (the project name is",
            "the repo's directory basename):",
            "",
            '- Before writing or changing code, call `playbook_get_guardrails(project="<basename>")`',
            '  for the always-on rules and `playbook_get_workflow(project="<basename>", intent="...")`',
            "  for the matching task workflow.",
            "- Read a language's rules with `playbook_get_standards`, reusable shapes with",
            "  `playbook_get_patterns`.",
            '- Before calling the work done, check `playbook_get_gates(project="<basename>")`.',
            "- If the repo has **no** dev-playbook project, do not edit code - run",
            "  `/dev-playbook-init` to configure it. Never substitute another project's standards.",
            END,
        ]
    )


def update_claude_md() -> str:
    CLAUDE_MD.parent.mkdir(parents=True, exist_ok=True)
    block = _block()
    existing = CLAUDE_MD.read_text(encoding="utf-8") if CLAUDE_MD.is_file() else ""
    if START in existing and END in existing:
        start = existing.index(START)
        end = existing.index(END) + len(END)
        new = existing[:start] + block + existing[end:]
        action = "updated"
    else:
        base = existing.rstrip("\n")
        new = (base + "\n\n" if base else "") + block + "\n"
        action = "added"
    CLAUDE_MD.write_text(new, encoding="utf-8")
    return action


def register_user_scope(url: str, token: str) -> tuple[bool, str]:
    spec: dict = {"type": "sse", "url": url}
    if token:
        spec["headers"] = {"Authorization": f"Bearer {token}"}
    try:
        # Remove first so a re-run replaces rather than errors on "already exists".
        subprocess.run(
            ["claude", "mcp", "remove", "--scope", "user", "dev-playbook"],
            capture_output=True,
            text=True,
            check=False,
        )
        result = subprocess.run(
            ["claude", "mcp", "add-json", "--scope", "user", "dev-playbook", json.dumps(spec)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False, (
            "the `claude` CLI is not on PATH; skipped the user-scope MCP entry. "
            f"Register it yourself: claude mcp add-json --scope user dev-playbook '{json.dumps(spec)}'"
        )
    ok = result.returncode == 0
    return ok, (result.stdout or result.stderr).strip()


def arm_enforce() -> None:
    path = playbook_db.enforce_marker()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("on\n", encoding="utf-8")


def mark_configured(project: str) -> Path:
    path = playbook_db.configured_marker(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("configured\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dev-playbook init helper")
    parser.add_argument("--server-url", default=DEFAULT_URL)
    parser.add_argument("--token", default="")
    parser.add_argument("--no-user-scope", action="store_true")
    parser.add_argument("--configured", metavar="PROJECT", default="")
    args = parser.parse_args(argv)

    report: dict = {}

    # Marking a repo configured is a standalone step, run after a project exists.
    if args.configured:
        path = mark_configured(args.configured)
        print(json.dumps({"configured": args.configured, "marker": str(path)}))
        return 0

    report["claude_md"] = update_claude_md()
    report["claude_md_path"] = str(CLAUDE_MD)

    arm_enforce()
    report["enforce_marker"] = str(playbook_db.enforce_marker())

    if args.no_user_scope:
        report["user_scope"] = "skipped"
    else:
        ok, detail = register_user_scope(args.server_url.strip() or DEFAULT_URL, args.token.strip())
        report["user_scope"] = "registered" if ok else "failed"
        report["user_scope_detail"] = detail
        report["server_url"] = args.server_url.strip() or DEFAULT_URL

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
