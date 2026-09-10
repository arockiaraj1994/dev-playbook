"""SessionStart hook - put this project's guardrails in context.

The MCP tools can fetch the guardrails, but only if the model thinks to ask.
Guardrails that arrive after the first edit are guardrails that did not work,
so they go in at session start whether anyone asked or not.

Reads the standards DB directly with stdlib sqlite3: no MCP round-trip, no
dependency on a running server, nothing to time out.

Contract: JSON hook payload on stdin, JSON on stdout, exit 0. Any failure at
all exits 0 with no output - a hook must never be the reason someone's session
is broken.
"""

from __future__ import annotations

import json
import sys

import playbook_db

GUARDRAILS_PATH = "core/guardrails.md"

# Long guardrails documents exist. Injecting one whole would crowd out the
# session it is meant to inform, so it is trimmed and the model is told where
# the rest is.
MAX_CHARS = 6000


def _emit(context: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


def _no_project_notice(cwd_name: str) -> str:
    return (
        f"[dev-playbook] No standards project named '{cwd_name}'. "
        "If this repo should have coding standards, run "
        "/dev-playbook:scaffold-standards to create them. "
        "Do not substitute another project's standards."
    )


def build_context(payload: dict) -> str | None:
    cwd = str(payload.get("cwd") or "")
    conn = playbook_db.connect()
    if conn is None:
        return None
    try:
        project = playbook_db.project_for_cwd(conn, cwd)
        if project is None:
            name = cwd.rsplit("/", 1)[-1] if cwd else "this directory"
            return _no_project_notice(name)

        body = playbook_db.read_doc(conn, project, GUARDRAILS_PATH)
    finally:
        conn.close()

    if not body:
        return (
            f"[dev-playbook] Standards project '{project}' has no {GUARDRAILS_PATH}. "
            f'Call playbook_find_standards(project="{project}") to see what it does have.'
        )

    text = playbook_db.strip_frontmatter(body)
    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS].rstrip()

    lines = [
        f"[dev-playbook] Coding standards are active for project '{project}'.",
        "",
        "Its guardrails follow. Apply them to every change you make in this repo.",
        f'Before writing code, call playbook_start_task(project="{project}", intent="...") '
        "for the workflow matching the task; check your work against "
        f'playbook_get_standard(project="{project}", ref="core/definition-of-done.md").',
        "",
        "---",
        "",
        text,
    ]
    if truncated:
        lines += [
            "",
            "---",
            f'[truncated] Read the whole document with playbook_get_standard(project="{project}", '
            f'ref="{GUARDRAILS_PATH}").',
        ]
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        context = build_context(payload)
    except Exception:  # noqa: BLE001 - see module docstring: never break a session
        return 0
    if context:
        _emit(context)
    return 0


if __name__ == "__main__":
    sys.exit(main())
