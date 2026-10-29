"""PreToolUse hook on Write|Edit - the definition of done, before the edit.

Two modes, chosen by the plugin's `enforce_standards` option:

  advisory (default)  Injects the project's definition of done as context, once
                      per session and project, and says so when the project
                      has no standards at all. Never blocks.
  enforcing           Additionally denies the edit when this repo has no
                      standards project, naming what is missing and how to
                      create it.

Advisory is the default because installing a plugin should not stop anyone's
work by surprise.

The repo is the one the edited file belongs to (its git root), not the
session's cwd, and the server is asked which project that is before any local
DB or marker. The advisory context goes in once per session and project, not on
every edit: repeating the
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

DOD_PATH = "gates/definition-of-done.md"
MAX_CHARS = 4000

# Set from the plugin's userConfig. Shell-form hook commands reject
# ${user_config.*}, so the value arrives as an environment variable instead.
ENFORCE_ENV = "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS"

_TRUE = {"1", "true", "yes", "on"}


def enforcing() -> bool:
    """Enforcement is on if the plugin option is set, or init armed the marker.

    The marker path lets `/dev-playbook-init` turn blocking on without the user
    editing plugin config, and works against a Dockerised server where there is
    no local DB to read.
    """
    if os.environ.get(ENFORCE_ENV, "").strip().lower() in _TRUE:
        return True
    return playbook_db.is_enforced()


def _marker_dir() -> Path:
    raw = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    base = Path(raw).expanduser() if raw else Path(tempfile.gettempdir())
    return base / "session-markers"


def _claim_session(session_id: str, project: str) -> bool:
    """True the first time this session asks about this project, False afterwards.

    Per project, because one session can edit several repos and each has its
    own definition of done. O_EXCL so two edits racing at startup cannot both
    claim it. A marker directory that cannot be written means we simply inject
    every time, which is noisy but never wrong.
    """
    if not session_id:
        return True
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:128]
    if not safe:
        return True
    key = "".join(c for c in project if c.isalnum() or c in "-_.")[:64] or "_"
    try:
        directory = _marker_dir()
        directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            str(directory / f"{safe}--{key}.marker"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
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
        "Run /dev-playbook-init to configure it (it checks the server and scaffolds "
        "standards if this repo has none).\n"
        "Do not substitute another project's standards."
    )


def _edited_path(payload: dict) -> str:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")


def _lookup(repo: Path) -> tuple[str | None, str | None, bool]:
    """(project, definition-of-done body, configured) for a repo.

    The server is asked first: it is the only source that knows a Dockerised
    server's projects. Only when it cannot be reached do the local DB and
    init's per-repo marker decide, as they did before the server could answer.
    """
    answer = playbook_db.server_lookup(repo.name)
    if answer is not None:
        if not answer["exists"]:
            return None, None, False
        return answer["project"], answer.get("definition_of_done"), True

    project = None
    body = None
    conn = playbook_db.connect()
    if conn is not None:
        try:
            project = playbook_db.project_for_name(conn, repo.name)
            body = playbook_db.read_doc(conn, project, DOD_PATH) if project else None
        finally:
            conn.close()
    return project, body, project is not None or playbook_db.is_configured_marked(str(repo))


def decide(payload: dict) -> dict | None:
    """The hook's whole decision. None means 'say nothing, allow the edit'.

    The repo is the one the edited file belongs to, not the session's cwd: a
    session started in a workspace folder edits several repos, and a file
    outside any repo (a memory note, a scratch file in /tmp) is not checked.
    """
    cwd = str(payload.get("cwd") or "")
    session_id = str(payload.get("session_id") or "")
    repo = playbook_db.repo_for_edit(_edited_path(payload), cwd)
    if repo is None:
        return None
    repo_name = repo.name or "this directory"

    project, body, configured = _lookup(repo)

    if not configured:
        # Not a known project. Block when enforcing; otherwise advise once so a
        # first-run user is never silently locked out.
        if enforcing():
            return _deny(f"[dev-playbook] {_missing_text(repo_name)}")
        if _claim_session(session_id, repo_name):
            return _context(f"[dev-playbook] {_missing_text(repo_name)}")
        return None

    if not _claim_session(session_id, project or repo_name):
        return None

    if not body:
        # Configured, but no definition of done to inject (an older server, or a
        # marker-only repo). Point at the tool rather than inject the text.
        who = project or repo_name
        return _context(
            f"[dev-playbook] Standards project '{who}' is configured. Check this "
            f'change against playbook_get_gates(project="{who}") before it is done.'
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
            f'[truncated] Read it whole with playbook_get_gates(project="{project}").',
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
