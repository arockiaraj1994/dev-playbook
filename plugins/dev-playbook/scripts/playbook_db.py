"""Shared read-only access to the dev-playbook standards, for the hooks.

Stdlib only, and deliberately so. Hooks run on every session start and every
edit, so they must not depend on uv resolving an environment. They ask the
server first (one GET with a short timeout - the only source that knows a
Dockerised server's projects), and fall back to reading a local standards DB
and init's markers, so a server that is down never breaks a session.

Read-only in every sense: opened with mode=ro, no writes, no migrations.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PLUGIN_NAME = "dev-playbook"


def _candidate_db_paths() -> list[Path]:
    """Where the standards DB might be, best guess first.

    Hooks do not receive the MCP server's `env` block from .mcp.json, so the
    path has to be rediscovered. Each candidate is a place the DB genuinely
    lands under one of the supported layouts; the first one that opens wins.
    """
    out: list[Path] = []

    def add(raw: str | None) -> None:
        if raw and raw.strip():
            out.append(Path(raw.strip()).expanduser())

    # An explicit override always wins - the escape hatch when discovery is wrong.
    add(os.environ.get("PLAYBOOK_DB_PATH"))

    data_dir = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    if data_dir:
        out.append(Path(data_dir).expanduser() / "playbook.db")

    add(os.environ.get("MCP_DB_PATH"))

    # The documented shape of CLAUDE_PLUGIN_DATA, for a host that does not
    # export it to hook processes.
    out.append(Path.home() / ".claude" / "plugins" / "data" / PLUGIN_NAME / "playbook.db")

    # A repo checkout run straight from source: the server's own default DB.
    root = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
    if root:
        out.append(Path(root).expanduser() / ".." / ".." / "mcp" / "data" / "metrics.db")

    return out


def _open(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='standards_files'"
        ).fetchone()
    except sqlite3.Error:
        conn.close()
        return None
    if row is None:
        conn.close()
        return None
    return conn


def connect() -> sqlite3.Connection | None:
    """First candidate DB that exists and carries the standards schema."""
    for path in _candidate_db_paths():
        try:
            resolved = path.resolve()
        except OSError:
            continue
        conn = _open(resolved)
        if conn is not None:
            return conn
    return None


def _fold(name: str) -> str:
    return "".join(c for c in name.lower() if c not in "-_. ")


def match_name(names: list[str], wanted: str) -> str | None:
    """The same matching the server's tools use (tools.common.match_project_name),
    so the hook and the model never disagree about which project a repo is:
    exact, then case-insensitive, then separator-insensitive, never a guess
    between two candidates."""
    if not wanted:
        return None
    if wanted in names:
        return wanted
    lowered = wanted.lower()
    for candidate in names:
        if candidate.lower() == lowered:
            return candidate
    folded = _fold(wanted)
    matches = [n for n in names if folded and _fold(n) == folded]
    return matches[0] if len(matches) == 1 else None


def project_for_name(conn: sqlite3.Connection, name: str) -> str | None:
    """The standards project in a local DB that a repo name refers to."""
    try:
        rows = conn.execute("SELECT name FROM standards_projects").fetchall()
    except sqlite3.Error:
        return None
    return match_name([r[0] for r in rows], name)


# ---------------------------------------------------------------------------
# Which repo is this?
#
# A session's cwd is often not the repo being edited: sessions start in a
# workspace folder (~/projects) or a subfolder (src/), and Codex starts in its
# own per-thread folder. The file being edited is the better witness.
# ---------------------------------------------------------------------------


def _not_a_repo_root() -> set[Path]:
    """Directories whose `.git` says nothing about the file being edited.

    A dotfiles repo at ~, or a stray /tmp/.git, would otherwise claim every
    edit beneath it, memory notes and scratch files included.
    """
    roots = {Path("/"), Path(tempfile.gettempdir())}
    try:
        roots.add(Path.home())
    except RuntimeError:
        pass
    return {r.resolve() for r in roots}


def git_root(path: Path) -> Path | None:
    """The nearest enclosing directory holding `.git` (a dir, or a worktree file)."""
    try:
        current = path if path.is_dir() else path.parent
        skip = _not_a_repo_root()
        for candidate in (current, *current.parents):
            if candidate.resolve() in skip:
                continue
            if (candidate / ".git").exists():
                return candidate
    except OSError:
        return None
    return None


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def repo_for_edit(file_path: str, cwd: str) -> Path | None:
    """The repo an edit lands in, or None when the file is not repo code.

    1. The file's git root.
    2. Else, when the file is under the cwd: the cwd itself (a repo without
       git yet), or - when the cwd is a workspace of repos - the top-level
       folder under it that holds the file (~/projects/trade-diary).
    3. Else None: a memory note, a /tmp scratch file - nothing to check.
    """
    cwd_path = Path(cwd).expanduser() if cwd else None
    if not file_path:
        return cwd_path
    target = Path(file_path).expanduser()
    if not target.is_absolute() and cwd_path is not None:
        target = cwd_path / target
    root = git_root(target)
    if root is not None:
        return root
    if cwd_path is None or not _is_within(target, cwd_path):
        return None
    if not _is_workspace(cwd_path):
        return cwd_path
    parts = target.relative_to(cwd_path).parts
    if len(parts) < 2:
        # A loose file in the workspace folder itself belongs to no repo.
        return None
    return cwd_path / parts[0]


def repo_for_session(cwd: str) -> Path | None:
    """The repo a session starts in, or None for a workspace of several repos.

    A cwd inside a git repo is that repo. A cwd that is not, but has repos
    directly below it, is a workspace: naming it would be the "no project
    named 'projects'" false alarm, so the edit gate decides per file instead.
    """
    if not cwd:
        return None
    path = Path(cwd).expanduser()
    root = git_root(path)
    if root is not None:
        return root
    if _is_workspace(path):
        return None
    return path


def _is_workspace(path: Path) -> bool:
    """A folder that is not itself in a repo but holds repos directly below it."""
    if git_root(path) is not None:
        return False
    try:
        return any((child / ".git").exists() for child in path.iterdir() if child.is_dir())
    except OSError:
        return False


# ---------------------------------------------------------------------------
# The server
#
# Against a Dockerised server the DB is in the container, so the hooks ask the
# server itself (GET /api/projects/{name}). Short timeout, and any failure
# reads as "no answer" so the caller falls back to the local DB and markers.
# ---------------------------------------------------------------------------

SERVER_URL_ENV = "PLAYBOOK_SERVER_URL"
# The plugin's userConfig, as Claude Code exports it to hook processes.
PLUGIN_SERVER_URL_ENV = "CLAUDE_PLUGIN_OPTION_SERVER_URL"
PLUGIN_TOKEN_ENV = "CLAUDE_PLUGIN_OPTION_TOKEN"
DEFAULT_SERVER_URL = "http://localhost:8420"
SERVER_TIMEOUT_S = 2.0


def server_base_url() -> str | None:
    """The server's base URL, or None when lookups are switched off ("off")."""
    raw = (
        os.environ.get(SERVER_URL_ENV, "").strip()
        or os.environ.get(PLUGIN_SERVER_URL_ENV, "").strip()
    )
    if raw.lower() == "off":
        return None
    url = (raw or DEFAULT_SERVER_URL).rstrip("/")
    return url.removesuffix("/sse")


def server_lookup(name: str) -> dict | None:
    """The server's answer for a repo name, or None if it could not be asked.

    The answer is {"exists": bool, "project", "guardrails", "definition_of_done"}.
    """
    base = server_base_url()
    if base is None or not name:
        return None
    if not base.startswith(("http://", "https://")):
        return None
    url = f"{base}/api/projects/{urllib.parse.quote(name, safe='')}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    token = os.environ.get(PLUGIN_TOKEN_ENV, "").strip()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=SERVER_TIMEOUT_S) as response:  # noqa: S310 - scheme checked above
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("exists"), bool):
        return None
    if data["exists"] and not isinstance(data.get("project"), str):
        return None
    for key in ("guardrails", "definition_of_done"):
        if not isinstance(data.get(key), (str, type(None))):
            data[key] = None
    return data


def read_doc(conn: sqlite3.Connection, project: str, relative_path: str) -> str | None:
    try:
        row = conn.execute(
            "SELECT body FROM standards_files WHERE project = ? AND relative_path = ?",
            (project, relative_path),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or not row[0]:
        return None
    return str(row[0])


# ---------------------------------------------------------------------------
# Markers
#
# With a Dockerised server the standards DB lives inside the container, so the
# hooks cannot read it from the host. `/dev-playbook-init` therefore drops small
# marker files the hooks can see without any DB or network access: one to arm
# enforcement, and one per repo it has configured. Both live under the plugin's
# persistent data dir, which init and the hooks both receive as
# CLAUDE_PLUGIN_DATA.
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    raw = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".claude" / "plugins" / "data" / PLUGIN_NAME


def _safe_name(cwd: str) -> str:
    name = Path(cwd).name if cwd else ""
    return "".join(c for c in name if c.isalnum() or c in "-_.") or "_"


def enforce_marker() -> Path:
    return data_dir() / "enforce"


def configured_marker(cwd: str) -> Path:
    return data_dir() / "configured" / _safe_name(cwd)


def is_enforced() -> bool:
    try:
        return enforce_marker().is_file()
    except OSError:
        return False


def is_configured_marked(cwd: str) -> bool:
    try:
        return bool(cwd) and configured_marker(cwd).is_file()
    except OSError:
        return False


def strip_frontmatter(body: str) -> str:
    """Drop a leading YAML frontmatter block.

    The frontmatter is addressing metadata for the tools; injecting it into a
    session spends tokens on `see_also:` lists nobody reads.
    """
    if not body.startswith("---"):
        return body.strip()
    end = body.find("\n---", 3)
    if end == -1:
        return body.strip()
    return body[end + 4 :].strip()
