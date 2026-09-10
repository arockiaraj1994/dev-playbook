"""Shared read-only access to the dev-playbook standards DB, for the hooks.

Stdlib only, and deliberately so. Hooks run on every session start and every
edit; making them depend on the MCP server being up, or on uv resolving an
environment, would mean a hook that fails exactly when the server is down. The
standards are rows in SQLite, so the hooks read the rows.

Read-only in every sense: opened with mode=ro, no writes, no migrations.
"""

from __future__ import annotations

import os
import sqlite3
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


def project_for_cwd(conn: sqlite3.Connection, cwd: str) -> str | None:
    """The standards project for a workspace, matched on directory basename.

    The same rule the tools use, so the hook and the model never disagree about
    which project this repo is: basename, case-insensitive, no substitutes.
    """
    name = Path(cwd).name if cwd else ""
    if not name:
        return None
    try:
        rows = conn.execute("SELECT name FROM standards_projects").fetchall()
    except sqlite3.Error:
        return None
    names = [r[0] for r in rows]
    if name in names:
        return name
    lowered = name.lower()
    for candidate in names:
        if candidate.lower() == lowered:
            return candidate
    return None


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
