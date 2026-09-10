"""The plugin's two hook scripts, driven through their real stdin JSON shapes.

These run as separate processes with only the stdlib, so they are tested as
separate processes: a fixture builds a standards DB with the same schema the
server writes, and each case pipes a hook payload in and reads JSON out.

The contract these protect is "a hook is never why a session is broken": every
failure mode has to exit 0, and only the enforcing path may ever deny.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "plugins" / "dev-playbook" / "scripts"
SESSION_CONTEXT = SCRIPTS / "session_context.py"
EDIT_GATE = SCRIPTS / "edit_gate.py"

GUARDRAILS = """---
title: Guardrails
description: what not to do
---

# Guardrails

- Never commit secrets.
- Every public function gets a test.
"""

DOD = """---
title: Definition of Done
---

# Definition of Done

- Tests pass.
- The changelog has an entry.
"""


def _make_db(path: Path, project: str, files: dict[str, str]) -> None:
    """A standards DB with the columns the hooks actually read.

    Deliberately not built through StandardsStore: the hooks are stdlib-only
    readers of this schema, so the test asserts against the schema rather than
    against the writer, and a column rename would surface here.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE standards_projects (
            name TEXT PRIMARY KEY,
            indicator TEXT NOT NULL DEFAULT 'green',
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE standards_files (
            project TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            frontmatter TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL,
            is_executable INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at INTEGER NOT NULL,
            updated_by TEXT,
            PRIMARY KEY (project, relative_path)
        );
        """
    )
    now = int(time.time())
    conn.execute("INSERT INTO standards_projects (name, updated_at) VALUES (?, ?)", (project, now))
    for rel, body in files.items():
        conn.execute(
            "INSERT INTO standards_files (project, relative_path, kind, title, body, updated_at)"
            " VALUES (?, ?, 'markdown', ?, ?, ?)",
            (project, rel, rel, body, now),
        )
    conn.commit()
    conn.close()


def _run(
    script: Path, payload: dict, env_extra: dict | None = None
) -> tuple[int, dict | None, str]:
    env = dict(os.environ)
    # Never let the developer's own machine leak into a hook test.
    for key in ("PLAYBOOK_DB_PATH", "CLAUDE_PLUGIN_DATA", "MCP_DB_PATH", "CLAUDE_PLUGIN_ROOT"):
        env.pop(key, None)
    env.pop("CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS", None)
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    out = proc.stdout.strip()
    return proc.returncode, (json.loads(out) if out else None), proc.stderr


@pytest.fixture
def workspace(tmp_path):
    """A repo directory, a matching standards project, and an isolated HOME.

    The isolated HOME matters: playbook_db falls back to
    ~/.claude/plugins/data/, and a test that found the developer's real DB
    would pass for the wrong reason.
    """
    repo = tmp_path / "billing-api"
    repo.mkdir()
    data = tmp_path / "plugin-data"
    home = tmp_path / "home"
    home.mkdir()
    _make_db(
        data / "playbook.db",
        "billing-api",
        {"core/guardrails.md": GUARDRAILS, "core/definition-of-done.md": DOD},
    )
    return {
        "repo": str(repo),
        "env": {"CLAUDE_PLUGIN_DATA": str(data), "HOME": str(home)},
        "data": data,
    }


def _session_payload(cwd: str, session_id: str = "sess-1") -> dict:
    return {
        "session_id": session_id,
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "hook_event_name": "SessionStart",
        "source": "startup",
    }


def _edit_payload(cwd: str, session_id: str = "sess-1") -> dict:
    return {
        "session_id": session_id,
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": f"{cwd}/src/main.py", "content": "x = 1\n"},
    }


# ---------------------------------------------------------------------------
# SessionStart
# ---------------------------------------------------------------------------


def test_session_start_injects_the_guardrails(workspace):
    code, out, _ = _run(SESSION_CONTEXT, _session_payload(workspace["repo"]), workspace["env"])
    assert code == 0
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "SessionStart"
    assert "Never commit secrets." in spec["additionalContext"]
    assert "billing-api" in spec["additionalContext"]


def test_session_start_strips_frontmatter(workspace):
    _, out, _ = _run(SESSION_CONTEXT, _session_payload(workspace["repo"]), workspace["env"])
    assert "description: what not to do" not in out["hookSpecificOutput"]["additionalContext"]


def test_session_start_matches_the_project_case_insensitively(tmp_path, workspace):
    # Its own parent, so a case-insensitive filesystem cannot collide this with
    # the fixture's lowercase billing-api directory.
    repo = tmp_path / "cased" / "Billing-API"
    repo.mkdir(parents=True)
    _, out, _ = _run(SESSION_CONTEXT, _session_payload(str(repo)), workspace["env"])
    assert "Never commit secrets." in out["hookSpecificOutput"]["additionalContext"]


def test_session_start_names_the_scaffold_skill_when_the_project_is_missing(tmp_path, workspace):
    other = tmp_path / "some-other-repo"
    other.mkdir()
    code, out, _ = _run(SESSION_CONTEXT, _session_payload(str(other)), workspace["env"])
    assert code == 0
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "/dev-playbook:scaffold-standards" in context
    assert "some-other-repo" in context
    # It must not offer the standards it did find as a substitute.
    assert "billing-api" not in context


def test_session_start_is_silent_when_there_is_no_database(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    code, out, _ = _run(
        SESSION_CONTEXT,
        _session_payload(str(tmp_path)),
        {"CLAUDE_PLUGIN_DATA": str(tmp_path / "nothing-here"), "HOME": str(home)},
    )
    assert code == 0
    assert out is None


def test_session_start_survives_garbage_on_stdin(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    proc = subprocess.run(
        [sys.executable, str(SESSION_CONTEXT)],
        input="not json at all",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_session_start_notes_a_project_with_no_guardrails(tmp_path):
    repo = tmp_path / "bare-repo"
    repo.mkdir()
    data = tmp_path / "plugin-data"
    home = tmp_path / "home"
    home.mkdir()
    _make_db(data / "playbook.db", "bare-repo", {"README.md": "# nothing useful"})
    _, out, _ = _run(
        SESSION_CONTEXT,
        _session_payload(str(repo)),
        {"CLAUDE_PLUGIN_DATA": str(data), "HOME": str(home)},
    )
    assert "no core/guardrails.md" in out["hookSpecificOutput"]["additionalContext"]


# ---------------------------------------------------------------------------
# PreToolUse
# ---------------------------------------------------------------------------


def test_edit_gate_injects_the_definition_of_done(workspace):
    code, out, _ = _run(EDIT_GATE, _edit_payload(workspace["repo"]), workspace["env"])
    assert code == 0
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert "The changelog has an entry." in spec["additionalContext"]
    assert "permissionDecision" not in spec


def test_edit_gate_speaks_once_per_session(workspace):
    first = _run(EDIT_GATE, _edit_payload(workspace["repo"]), workspace["env"])[1]
    second = _run(EDIT_GATE, _edit_payload(workspace["repo"]), workspace["env"])[1]
    assert first is not None
    assert second is None, "the definition of done was injected on a second edit"


def test_edit_gate_speaks_again_in_a_new_session(workspace):
    _run(EDIT_GATE, _edit_payload(workspace["repo"], "sess-a"), workspace["env"])
    out = _run(EDIT_GATE, _edit_payload(workspace["repo"], "sess-b"), workspace["env"])[1]
    assert out is not None


def test_edit_gate_is_advisory_by_default_when_the_project_is_missing(tmp_path, workspace):
    other = tmp_path / "unknown-repo"
    other.mkdir()
    code, out, _ = _run(EDIT_GATE, _edit_payload(str(other)), workspace["env"])
    assert code == 0
    spec = out["hookSpecificOutput"]
    assert "permissionDecision" not in spec
    assert "/dev-playbook:scaffold-standards" in spec["additionalContext"]


def test_edit_gate_denies_when_enforcing_and_the_project_is_missing(tmp_path, workspace):
    other = tmp_path / "unknown-repo"
    other.mkdir()
    env = {**workspace["env"], "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS": "true"}
    code, out, _ = _run(EDIT_GATE, _edit_payload(str(other)), env)
    assert code == 0
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    assert "/dev-playbook:scaffold-standards" in spec["permissionDecisionReason"]


def test_the_enforcing_deny_is_not_once_per_session(tmp_path, workspace):
    """A gate that only closes for the first edit is not a gate."""
    other = tmp_path / "unknown-repo"
    other.mkdir()
    env = {**workspace["env"], "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS": "true"}
    for _ in range(3):
        out = _run(EDIT_GATE, _edit_payload(str(other)), env)[1]
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_edit_gate_never_denies_when_the_project_has_standards(workspace):
    env = {**workspace["env"], "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS": "true"}
    out = _run(EDIT_GATE, _edit_payload(workspace["repo"]), env)[1]
    assert "permissionDecision" not in out["hookSpecificOutput"]


def test_edit_gate_does_not_block_when_there_is_no_database(tmp_path):
    """Enforcing on a machine with nothing scaffolded would lock a new user out."""
    home = tmp_path / "home"
    home.mkdir()
    code, out, _ = _run(
        EDIT_GATE,
        _edit_payload(str(tmp_path)),
        {
            "CLAUDE_PLUGIN_DATA": str(tmp_path / "nothing-here"),
            "HOME": str(home),
            "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS": "true",
        },
    )
    assert code == 0
    assert "permissionDecision" not in (out or {}).get("hookSpecificOutput", {})


@pytest.mark.parametrize("value", ["", "false", "0", "no", "off", "maybe"])
def test_enforcement_stays_off_for_anything_but_a_true_value(tmp_path, workspace, value):
    other = tmp_path / "unknown-repo"
    other.mkdir()
    env = {**workspace["env"], "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS": value}
    out = _run(EDIT_GATE, _edit_payload(str(other)), env)[1]
    assert "permissionDecision" not in out["hookSpecificOutput"]


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_enforcement_turns_on_for_the_usual_true_spellings(tmp_path, workspace, value):
    other = tmp_path / "unknown-repo"
    other.mkdir()
    env = {**workspace["env"], "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS": value}
    out = _run(EDIT_GATE, _edit_payload(str(other)), env)[1]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_edit_gate_survives_garbage_on_stdin(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    proc = subprocess.run(
        [sys.executable, str(EDIT_GATE)],
        input="{{{",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_an_explicit_db_path_overrides_discovery(tmp_path, workspace):
    """PLAYBOOK_DB_PATH is the escape hatch when discovery guesses wrong."""
    home = tmp_path / "explicit-home"
    home.mkdir()
    code, out, _ = _run(
        SESSION_CONTEXT,
        _session_payload(workspace["repo"]),
        {"PLAYBOOK_DB_PATH": str(workspace["data"] / "playbook.db"), "HOME": str(home)},
    )
    assert code == 0
    assert "Never commit secrets." in out["hookSpecificOutput"]["additionalContext"]


# ---------------------------------------------------------------------------
# hooks.json - the wiring, not the scripts
#
# A malformed hook entry is not a load error: it is dropped with "entry ignored
# at runtime" and the hook simply never fires. Nothing says so, which is why the
# shape is asserted here rather than left to be noticed in a session.
# ---------------------------------------------------------------------------

HOOKS_JSON = SCRIPTS.parent / "hooks" / "hooks.json"


def _hook_entries() -> list[tuple[str, dict]]:
    data = json.loads(HOOKS_JSON.read_text())
    return [
        (event, entry)
        for event, matchers in data["hooks"].items()
        for matcher in matchers
        for entry in matcher["hooks"]
    ]


def test_both_events_are_wired():
    data = json.loads(HOOKS_JSON.read_text())
    assert set(data["hooks"]) == {"SessionStart", "PreToolUse"}


def test_the_edit_gate_matches_write_and_edit():
    data = json.loads(HOOKS_JSON.read_text())
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "Write|Edit"


def test_every_hook_uses_shell_form():
    """Exec form ('args') validates fine and is then silently discarded."""
    for event, entry in _hook_entries():
        assert entry["type"] == "command", event
        assert isinstance(entry.get("command"), str) and entry["command"].strip(), event
        assert "args" not in entry, f"{event} uses exec form, which never fires"


def test_every_hook_runs_a_script_that_exists():
    for event, entry in _hook_entries():
        named = [s for s in ("session_context.py", "edit_gate.py") if s in entry["command"]]
        assert named, f"{event} names no known hook script"
        for script in named:
            assert (SCRIPTS / script).is_file(), f"{event} runs missing {script}"
