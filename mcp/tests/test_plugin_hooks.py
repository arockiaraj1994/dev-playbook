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
    for key in (
        "PLAYBOOK_DB_PATH",
        "CLAUDE_PLUGIN_DATA",
        "MCP_DB_PATH",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS",
        "CLAUDE_PLUGIN_OPTION_SERVER_URL",
        "CLAUDE_PLUGIN_OPTION_TOKEN",
    ):
        env.pop(key, None)
    # Nor a real server on localhost: server lookups are off unless a test
    # points them at its own fake server.
    env["PLAYBOOK_SERVER_URL"] = "off"
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
        {"guardrails.md": GUARDRAILS, "gates/definition-of-done.md": DOD},
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


def test_session_start_names_init_when_the_project_is_missing(tmp_path, workspace):
    other = tmp_path / "some-other-repo"
    other.mkdir()
    code, out, _ = _run(SESSION_CONTEXT, _session_payload(str(other)), workspace["env"])
    assert code == 0
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "/dev-playbook-init" in context
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
    assert "no guardrails.md" in out["hookSpecificOutput"]["additionalContext"]


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
    assert "/dev-playbook-init" in spec["additionalContext"]


def test_edit_gate_denies_when_enforcing_and_the_project_is_missing(tmp_path, workspace):
    other = tmp_path / "unknown-repo"
    other.mkdir()
    env = {**workspace["env"], "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS": "true"}
    code, out, _ = _run(EDIT_GATE, _edit_payload(str(other)), env)
    assert code == 0
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    assert "/dev-playbook-init" in spec["permissionDecisionReason"]


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


def test_edit_gate_stays_advisory_with_no_database_and_no_enforcement(tmp_path):
    """Plugin installed, nothing configured, enforcement off: never block."""
    home = tmp_path / "home"
    home.mkdir()
    code, out, _ = _run(
        EDIT_GATE,
        _edit_payload(str(tmp_path)),
        {"CLAUDE_PLUGIN_DATA": str(tmp_path / "nothing-here"), "HOME": str(home)},
    )
    assert code == 0
    assert "permissionDecision" not in (out or {}).get("hookSpecificOutput", {})


def test_edit_gate_denies_in_docker_mode_when_enforcing_and_unconfigured(tmp_path):
    """No local DB (server is in a container). Enforcing + no marker → block."""
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "some-repo"
    repo.mkdir()
    code, out, _ = _run(
        EDIT_GATE,
        _edit_payload(str(repo)),
        {
            "CLAUDE_PLUGIN_DATA": str(tmp_path / "empty-data"),
            "HOME": str(home),
            "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS": "true",
        },
    )
    assert code == 0
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "/dev-playbook-init" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_edit_gate_allows_when_a_configured_marker_exists_without_a_database(tmp_path):
    """The per-repo marker from /dev-playbook-init clears the block in Docker mode."""
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    (data / "configured").mkdir(parents=True)
    (data / "configured" / "billing-api").write_text("configured\n")
    repo = tmp_path / "billing-api"
    repo.mkdir()
    _code, out, _ = _run(
        EDIT_GATE,
        _edit_payload(str(repo)),
        {
            "CLAUDE_PLUGIN_DATA": str(data),
            "HOME": str(home),
            "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS": "true",
        },
    )
    assert "permissionDecision" not in (out or {}).get("hookSpecificOutput", {})


def test_enforce_marker_arms_enforcement_without_the_plugin_option(tmp_path):
    """/dev-playbook-init arms blocking via a marker, not the plugin option."""
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "enforce").write_text("on\n")
    repo = tmp_path / "some-repo"
    repo.mkdir()
    _code, out, _ = _run(
        EDIT_GATE,
        _edit_payload(str(repo)),
        {"CLAUDE_PLUGIN_DATA": str(data), "HOME": str(home)},
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


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
# Which repo - the edited file's git root, not the session's cwd (#635)
# ---------------------------------------------------------------------------


def _git_repo(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    return path


def _edit_file_payload(cwd: str, file_path: str, session_id: str = "sess-1") -> dict:
    payload = _edit_payload(cwd, session_id)
    payload["tool_input"] = {"file_path": file_path, "content": "x = 1\n"}
    return payload


def test_edit_gate_names_the_repo_of_the_edited_file_not_the_workspace_cwd(tmp_path, workspace):
    """A session started in ~/projects editing ~/projects/billing-api."""
    projects = tmp_path / "projects"
    repo = _git_repo(projects / "billing-api")
    payload = _edit_file_payload(str(projects), str(repo / "src" / "main.py"))
    out = _run(EDIT_GATE, payload, workspace["env"])[1]
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "The changelog has an entry." in context
    assert "'projects'" not in context


def test_edit_gate_uses_the_git_root_from_a_subfolder_cwd(tmp_path, workspace):
    repo = _git_repo(tmp_path / "nested" / "billing-api")
    sub = repo / "src"
    sub.mkdir()
    out = _run(EDIT_GATE, _edit_file_payload(str(sub), str(sub / "main.py")), workspace["env"])[1]
    assert "The changelog has an entry." in out["hookSpecificOutput"]["additionalContext"]


def test_edit_gate_ignores_a_file_outside_any_repo_even_when_enforcing(tmp_path, workspace):
    """A memory note or /tmp scratch file is not repo code: no notice, no deny."""
    projects = tmp_path / "projects"
    projects.mkdir()
    note = tmp_path / "home" / ".claude" / "memory" / "note.md"
    env = {**workspace["env"], "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS": "true"}
    code, out, _ = _run(EDIT_GATE, _edit_file_payload(str(projects), str(note)), env)
    assert code == 0
    assert out is None


def test_a_dotfiles_repo_at_home_does_not_claim_every_edit(tmp_path, workspace):
    """~/.git (a dotfiles repo) must not make a memory note look like repo code."""
    home = _git_repo(tmp_path / "dotfiles-home")
    projects = home / "projects"
    projects.mkdir()
    note = home / ".claude" / "memory" / "note.md"
    env = {**workspace["env"], "HOME": str(home), "CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS": "true"}
    code, out, _ = _run(EDIT_GATE, _edit_file_payload(str(projects), str(note)), env)
    assert code == 0
    assert out is None


@pytest.mark.parametrize("dirname", ["billing_api", "BillingAPI", "billing.api"])
def test_edit_gate_matches_the_project_ignoring_separators(tmp_path, workspace, dirname):
    repo = _git_repo(tmp_path / "sep" / dirname)
    out = _run(EDIT_GATE, _edit_file_payload(str(repo), str(repo / "a.py")), workspace["env"])[1]
    assert "The changelog has an entry." in out["hookSpecificOutput"]["additionalContext"]


def test_edit_gate_speaks_once_per_project_not_once_per_session(tmp_path):
    """One session editing two configured repos gets both definitions of done."""
    data = tmp_path / "plugin-data"
    home = tmp_path / "home"
    home.mkdir()
    db = data / "playbook.db"
    _make_db(db, "alpha", {"gates/definition-of-done.md": "# DoD\n\n- alpha rule\n"})
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO standards_projects (name, updated_at) VALUES ('beta', 0)")
    conn.execute(
        "INSERT INTO standards_files (project, relative_path, kind, title, body, updated_at)"
        " VALUES ('beta', 'gates/definition-of-done.md', 'markdown', 'd', '# DoD\n\n- beta rule\n', 0)"
    )
    conn.commit()
    conn.close()
    projects = tmp_path / "projects"
    alpha = _git_repo(projects / "alpha")
    beta = _git_repo(projects / "beta")
    env = {"CLAUDE_PLUGIN_DATA": str(data), "HOME": str(home)}
    first = _run(EDIT_GATE, _edit_file_payload(str(projects), str(alpha / "a.py")), env)[1]
    second = _run(EDIT_GATE, _edit_file_payload(str(projects), str(beta / "b.py")), env)[1]
    again = _run(EDIT_GATE, _edit_file_payload(str(projects), str(alpha / "c.py")), env)[1]
    assert "alpha rule" in first["hookSpecificOutput"]["additionalContext"]
    assert "beta rule" in second["hookSpecificOutput"]["additionalContext"]
    assert again is None


def test_session_start_is_silent_in_a_workspace_of_repos(tmp_path, workspace):
    projects = tmp_path / "projects"
    _git_repo(projects / "billing-api")
    _git_repo(projects / "other")
    code, out, _ = _run(SESSION_CONTEXT, _session_payload(str(projects)), workspace["env"])
    assert code == 0
    assert out is None


def test_session_start_uses_the_git_root_from_a_subfolder(tmp_path, workspace):
    repo = _git_repo(tmp_path / "deep" / "billing-api")
    sub = repo / "deploy"
    sub.mkdir()
    out = _run(SESSION_CONTEXT, _session_payload(str(sub)), workspace["env"])[1]
    assert "Never commit secrets." in out["hookSpecificOutput"]["additionalContext"]


# ---------------------------------------------------------------------------
# Asking the server - GET /api/projects/{name} (#635)
# ---------------------------------------------------------------------------


class _FakeServer:
    """A stand-in for the dev-playbook server's lookup endpoint."""

    def __init__(self, projects: dict[str, dict]) -> None:
        import http.server
        import threading

        self.projects = projects
        self.requests: list[tuple[str, str | None]] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                outer.requests.append((self.path, self.headers.get("Authorization")))
                name = self.path.rsplit("/", 1)[-1]
                found = outer.projects.get(name)
                body = {"exists": True, **found} if found else {"exists": False, "project": None}
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_args: object) -> None:
                pass

        self._httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def fake_server():
    server = _FakeServer(
        {
            "mytrip": {
                "project": "my-trip",
                "guardrails": "# Guardrails\n\n- Server guardrail rule.\n",
                "definition_of_done": "---\ntitle: DoD\n---\n\n# DoD\n\n- Server DoD rule.\n",
            }
        }
    )
    yield server
    server.close()


def _no_db_env(tmp_path: Path, server_url: str, **extra: str) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "empty-data"),
        "HOME": str(home),
        "PLAYBOOK_SERVER_URL": server_url,
        **extra,
    }


def test_edit_gate_takes_the_definition_of_done_from_the_server(tmp_path, fake_server):
    """No local DB at all (Docker server): the server's answer is used."""
    repo = _git_repo(tmp_path / "projects" / "mytrip")
    env = _no_db_env(
        tmp_path, f"{fake_server.url}/sse", CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS="true"
    )
    payload = _edit_file_payload(str(tmp_path / "projects"), str(repo / "app.kt"))
    out = _run(EDIT_GATE, payload, env)[1]
    spec = out["hookSpecificOutput"]
    assert "permissionDecision" not in spec
    assert "Server DoD rule." in spec["additionalContext"]
    assert "'my-trip'" in spec["additionalContext"]
    assert "title: DoD" not in spec["additionalContext"]
    assert fake_server.requests[0][0] == "/api/projects/mytrip"


def test_session_start_takes_the_guardrails_from_the_server(tmp_path, fake_server):
    repo = _git_repo(tmp_path / "mytrip")
    out = _run(SESSION_CONTEXT, _session_payload(str(repo)), _no_db_env(tmp_path, fake_server.url))[
        1
    ]
    assert "Server guardrail rule." in out["hookSpecificOutput"]["additionalContext"]


def test_the_server_saying_no_project_wins_over_a_stale_marker(tmp_path, fake_server):
    data = tmp_path / "empty-data"
    (data / "configured").mkdir(parents=True)
    (data / "configured" / "gone").write_text("configured\n")
    repo = _git_repo(tmp_path / "gone")
    env = _no_db_env(tmp_path, fake_server.url, CLAUDE_PLUGIN_OPTION_ENFORCE_STANDARDS="true")
    out = _run(EDIT_GATE, _edit_file_payload(str(repo), str(repo / "x.py")), env)[1]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_plugin_token_is_sent_as_a_bearer_header(tmp_path, fake_server):
    repo = _git_repo(tmp_path / "mytrip")
    env = _no_db_env(tmp_path, fake_server.url, CLAUDE_PLUGIN_OPTION_TOKEN="tok-123")
    _run(EDIT_GATE, _edit_file_payload(str(repo), str(repo / "x.py")), env)
    assert fake_server.requests[0][1] == "Bearer tok-123"


def test_an_unreachable_server_falls_back_to_the_local_db(tmp_path, workspace):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{s.getsockname()[1]}"
    env = {**workspace["env"], "PLAYBOOK_SERVER_URL": dead}
    out = _run(EDIT_GATE, _edit_payload(workspace["repo"]), env)[1]
    assert "The changelog has an entry." in out["hookSpecificOutput"]["additionalContext"]


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
