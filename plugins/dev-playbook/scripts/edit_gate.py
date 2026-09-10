"""PreToolUse hook on Write|Edit - the definition of done, before the edit.

Two modes, chosen by the plugin's `enforce_standards` option:

  advisory (default)  Injects the project's definition of done as context, once
                      per session, and says so when the project has no
                      standards at all. Never blocks.
  enforcing           Additionally denies the edit when this repo has no
                      standards project, naming what is missing and how to
                      create it.

Advisory is the default because installing a plugin should not stop anyone's
work by surprise.

The advisory context goes in once per session, not on every edit: repeating the
definition of done on the fortieth edit teaches nothing and costs tokens every
time. The deny, by contrast, is evaluated on every call - a gate that only
closes for the first edit is not a gate.

Contract: JSON hook payload on stdin, JSON on stdout, exit 0. Any failure exits
0 with no output; a broken hook must not be able to block an edit.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import playbook_db

DOD_PATH = "core/definition-of-done.md"
MAX_CHARS = 4000

# Set from the plugin's userConfig. Shell-form hook commands reject
# ${user_config.*}, so the value arrives as an environment variable instead.
ENFORCE_ENV = "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS"

_TRUE = {"1", "true", "yes", "on"}


def enforcing() -> bool:
    return os.environ.get(ENFORCE_ENV, "").strip().lower() in _TRUE


def _marker_dir() -> Path:
    raw = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    base = Path(raw).expanduser() if raw else Path(tempfile.gettempdir())
    return base / "session-markers"


def _claim_session(session_id: str) -> bool:
    """True the first time this session asks, False afterwards.

    O_EXCL so two edits racing at startup cannot both claim it. A marker
    directory that cannot be written means we simply inject every time, which
    is noisy but never wrong.
    """
    if not session_id:
        return True
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:128]
    if not safe:
        return True
    try:
        directory = _marker_dir()
        directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(directory / f"{safe}.marker"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except OSError:
        return True
    os.close(fd)
    return True


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")


def _context(text: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": text,
        }
    }


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _missing_text(name: str) -> str:
    return (
        f"This repo has no dev-playbook standards project named '{name}', so there "
        "is no definition of done to check this change against.\n"
        "Create one with /dev-playbook:scaffold-standards, or call "
        "playbook_list_templates() and then playbook_scaffold_standards(dry_run=true).\n"
        "Do not substitute another project's standards."
    )


def decide(payload: dict) -> dict | None:
    """The hook's whole decision. None means 'say nothing, allow the edit'."""
    cwd = str(payload.get("cwd") or "")
    session_id = str(payload.get("session_id") or "")
    cwd_name = Path(cwd).name if cwd else "this directory"

    conn = playbook_db.connect()
    if conn is None:
        # No standards DB at all: the plugin is installed but nothing has been
        # scaffolded yet. Enforcing on that would block a first-run user out of
        # their own repo, so it is only ever advisory.
        if enforcing() and _claim_session(session_id):
            return _context(
                "[dev-playbook] Standards enforcement is on, but no standards database "
                "was found, so nothing can be checked. Run "
                "/dev-playbook:scaffold-standards to create this repo's standards."
            )
        return None

    try:
        project = playbook_db.project_for_cwd(conn, cwd)
        body = playbook_db.read_doc(conn, project, DOD_PATH) if project else None
    finally:
        conn.close()

    if project is None:
        if enforcing():
            return _deny(f"[dev-playbook] {_missing_text(cwd_name)}")
        if _claim_session(session_id):
            return _context(f"[dev-playbook] {_missing_text(cwd_name)}")
        return None

    if not _claim_session(session_id):
        return None

    if not body:
        return _context(
            f"[dev-playbook] Standards project '{project}' has no {DOD_PATH}. "
            f'Call playbook_find_standards(project="{project}") to see what it does have.'
        )

    text = playbook_db.strip_frontmatter(body)
    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS].rstrip()

    lines = [
        f"[dev-playbook] Definition of done for '{project}'. This change is not "
        "finished until it meets these.",
        "",
        text,
    ]
    if truncated:
        lines += [
            "",
            f'[truncated] Read it whole with playbook_get_standard(project="{project}", '
            f'ref="{DOD_PATH}").',
        ]
    return _context("\n".join(lines))


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        result = decide(payload)
    except Exception:  # noqa: BLE001 - see module docstring: never block an edit
        return 0
    if result:
        _emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
