"""SessionStart hook - put this project's guardrails in context.

The MCP tools can fetch the guardrails, but only if the model thinks to ask.
Guardrails that arrive after the first edit are guardrails that did not work,
so they go in at session start whether anyone asked or not.

Asks the server (one short GET) for the repo's guardrails, and falls back to a
local standards DB with stdlib sqlite3 when the server cannot be reached. The
repo is the cwd's git root; a workspace folder holding several repos gets no
notice, because there is no single project to name.

Contract: JSON hook payload on stdin, JSON on stdout, exit 0. Any failure at
all exits 0 with no output - a hook must never be the reason someone's session
is broken.
"""

from __future__ import annotations

import json
import sys

import playbook_db

GUARDRAILS_PATH = "guardrails.md"

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
        "If this repo should have coding standards, run /dev-playbook-init to "
        "configure it. Do not substitute another project's standards."
    )


def _configured_note(project: str) -> str:
    """Docker mode: the DB is in the container, so point at the tools instead of
    injecting the guardrails text the host cannot read."""
    return (
        f"[dev-playbook] Coding standards are active for project '{project}'. Before "
        f'writing code, call playbook_get_guardrails(project="{project}") for the '
        f'always-on rules and playbook_get_workflow(project="{project}", intent="...") '
        "for the task workflow. Apply them to every change in this repo."
    )


def build_context(payload: dict) -> str | None:
    cwd = str(payload.get("cwd") or "")
    repo = playbook_db.repo_for_session(cwd)
    if repo is None:
        # A workspace of several repos (or no cwd): there is no one project to
        # name yet. The edit gate decides per file once editing starts.
        return None
    name = repo.name or "this directory"

    answer = playbook_db.server_lookup(name)
    if answer is not None:
        if not answer["exists"]:
            return _no_project_notice(name)
        project = answer["project"]
        body = answer.get("guardrails")
    else:
        conn = playbook_db.connect()
        project = None
        body = None
        if conn is not None:
            try:
                project = playbook_db.project_for_name(conn, name)
                body = playbook_db.read_doc(conn, project, GUARDRAILS_PATH) if project else None
            finally:
                conn.close()

        if project is None:
            # No server answer and no local DB project. A per-repo marker from
            # /dev-playbook-init means it is configured - point at the tools.
            # With no DB and no marker, stay quiet.
            if playbook_db.is_configured_marked(str(repo)):
                return _configured_note(name)
            if conn is None:
                return None
            return _no_project_notice(name)

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
        f'Before writing code, call playbook_get_workflow(project="{project}", intent="...") '
        "for the workflow matching the task; check your work against "
        f'playbook_get_gates(project="{project}").',
        "",
        "---",
        "",
        text,
    ]
    if truncated:
        lines += [
            "",
            "---",
            f'[truncated] Read the whole document with playbook_get_guardrails(project="{project}").',
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
