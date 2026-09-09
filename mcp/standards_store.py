"""
standards_store.py - SQLite-backed storage for the standards/ corpus.

Replaces the filesystem walk in standards_scanner.py with two tables added to
the same DB file used by MetricsStore (one volume mount is the full state).
Sync SQLite behind async methods via `asyncio.to_thread`, mirroring the style
of metrics.py.

All SQL uses parameterized queries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS standards_projects (
        name           TEXT PRIMARY KEY,
        indicator      TEXT NOT NULL DEFAULT 'green',
        updated_at     INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS standards_files (
        project        TEXT NOT NULL,
        relative_path  TEXT NOT NULL,
        kind           TEXT NOT NULL,
        title          TEXT NOT NULL,
        description    TEXT NOT NULL DEFAULT '',
        frontmatter    TEXT NOT NULL DEFAULT '',
        body           TEXT NOT NULL,
        is_executable  INTEGER NOT NULL DEFAULT 0,
        version        INTEGER NOT NULL DEFAULT 1,
        updated_at     INTEGER NOT NULL,
        updated_by     TEXT,
        PRIMARY KEY (project, relative_path),
        FOREIGN KEY (project) REFERENCES standards_projects(name) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_standards_files_project ON standards_files(project)",
)

# Added by migration rather than in _SCHEMA, so existing DBs pick them up too.
# All are nullable TEXT: a hand-made project simply has no provenance.
_PROJECT_PROVENANCE_COLUMNS = (
    "template_id",
    "template_version",
    "language",
    "language_version",
    # JSON array of every pack that took part: [{id, kind, version}, ...].
    # template_id/language hold the first selected language, for display.
    "packs",
)

# source_hash is the sha256 of the content as scaffolded. Comparing it to a
# re-hash of the current body is what later distinguishes "untouched, safe to
# update from upstream" from "locally edited, needs a merge".
_FILE_PROVENANCE_COLUMNS = (
    "source_template",
    "source_version",
    "source_hash",
    "source_rule_ids",
    # Which pack produced this document - base, or a specific language.
    "source_pack",
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectRow:
    name: str
    indicator: str
    updated_at: int
    # Provenance: set when the project was scaffolded from a template, else None.
    template_id: str | None = None
    template_version: str | None = None
    language: str | None = None
    language_version: str | None = None
    packs: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class FileRow:
    project: str
    relative_path: str
    kind: str  # "markdown" | "script"
    title: str
    description: str
    frontmatter: str
    body: str
    is_executable: bool
    version: int
    updated_at: int
    updated_by: str | None


class ProjectExists(Exception):
    """Raised when scaffolding would overwrite an existing project."""

    def __init__(self, project: str) -> None:
        self.project = project
        super().__init__(f"project '{project}' already exists")


class VersionConflict(Exception):
    """Raised by upsert_file when expected_version doesn't match the stored row."""

    def __init__(self, project: str, relative_path: str, expected: int, actual: int) -> None:
        self.project = project
        self.relative_path = relative_path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"version conflict on {project}/{relative_path}: "
            f"expected {expected}, stored is {actual}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _connect(path: Path):
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit mode
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def _row_to_file(r: sqlite3.Row) -> FileRow:
    return FileRow(
        project=r["project"],
        relative_path=r["relative_path"],
        kind=r["kind"],
        title=r["title"],
        description=r["description"],
        frontmatter=r["frontmatter"],
        body=r["body"],
        is_executable=bool(r["is_executable"]),
        version=r["version"],
        updated_at=r["updated_at"],
        updated_by=r["updated_by"],
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class StandardsStore:
    """Thread-safe-via-asyncio.to_thread wrapper over a SQLite file."""

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)

    @property
    def path(self) -> Path:
        return self._path

    # -- lifecycle ------------------------------------------------------------

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)
        logger.info("Standards store initialized at %s", self._path)

    def _init_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._path) as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            # Migrate: template provenance columns. _SCHEMA only ever CREATEs, so
            # DBs made before templates existed need these added in place.
            for table, columns in (
                ("standards_projects", _PROJECT_PROVENANCE_COLUMNS),
                ("standards_files", _FILE_PROVENANCE_COLUMNS),
            ):
                existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                for column in columns:
                    if column not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

    # -- readers ----------------------------------------------------------------

    async def list_projects(self) -> list[str]:
        return await asyncio.to_thread(self._list_projects_sync)

    def _list_projects_sync(self) -> list[str]:
        with _connect(self._path) as conn:
            rows = conn.execute("SELECT name FROM standards_projects ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    async def get_project(self, name: str) -> ProjectRow | None:
        return await asyncio.to_thread(self._get_project_sync, name)

    def _get_project_sync(self, name: str) -> ProjectRow | None:
        with _connect(self._path) as conn:
            row = conn.execute(
                """
                SELECT name, indicator, updated_at, template_id, template_version,
                       language, language_version, packs
                FROM standards_projects WHERE name = ?
                """,
                (name,),
            ).fetchone()
        if row is None:
            return None
        return ProjectRow(
            name=row["name"],
            indicator=row["indicator"],
            updated_at=row["updated_at"],
            template_id=row["template_id"],
            template_version=row["template_version"],
            language=row["language"],
            language_version=row["language_version"],
            packs=json.loads(row["packs"]) if row["packs"] else [],
        )

    async def list_files(self, project: str) -> list[FileRow]:
        return await asyncio.to_thread(self._list_files_sync, project)

    def _list_files_sync(self, project: str) -> list[FileRow]:
        with _connect(self._path) as conn:
            rows = conn.execute(
                """
                SELECT project, relative_path, kind, title, description, frontmatter,
                       body, is_executable, version, updated_at, updated_by
                FROM standards_files
                WHERE project = ?
                ORDER BY relative_path
                """,
                (project,),
            ).fetchall()
        return [_row_to_file(r) for r in rows]

    async def get_file(self, project: str, relative_path: str) -> FileRow | None:
        return await asyncio.to_thread(self._get_file_sync, project, relative_path)

    def _get_file_sync(self, project: str, relative_path: str) -> FileRow | None:
        with _connect(self._path) as conn:
            row = conn.execute(
                """
                SELECT project, relative_path, kind, title, description, frontmatter,
                       body, is_executable, version, updated_at, updated_by
                FROM standards_files
                WHERE project = ? AND relative_path = ?
                """,
                (project, relative_path),
            ).fetchone()
        return _row_to_file(row) if row is not None else None

    # -- writers ------------------------------------------------------------

    async def upsert_file(
        self,
        *,
        project: str,
        relative_path: str,
        kind: str,
        title: str,
        description: str = "",
        frontmatter: str = "",
        body: str,
        is_executable: bool = False,
        expected_version: int | None,
        updated_by: str | None = None,
    ) -> FileRow:
        """Insert or update a file row.

        `expected_version` is the version the caller last read. `None` means
        "create new" and fails if the row already exists (also a conflict:
        the caller thought this path was free). When updating, it must match
        the row's current `version` or `VersionConflict` is raised.
        """
        return await asyncio.to_thread(
            self._upsert_file_sync,
            project,
            relative_path,
            kind,
            title,
            description,
            frontmatter,
            body,
            is_executable,
            expected_version,
            updated_by,
        )

    def _upsert_file_sync(
        self,
        project: str,
        relative_path: str,
        kind: str,
        title: str,
        description: str,
        frontmatter: str,
        body: str,
        is_executable: bool,
        expected_version: int | None,
        updated_by: str | None,
    ) -> FileRow:
        now = int(time.time())
        with _connect(self._path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT version FROM standards_files WHERE project = ? AND relative_path = ?",
                    (project, relative_path),
                ).fetchone()

                if existing is None:
                    if expected_version is not None:
                        raise VersionConflict(project, relative_path, expected_version, 0)
                    conn.execute(
                        """
                        INSERT INTO standards_projects (name, indicator, updated_at)
                        VALUES (?, 'green', ?)
                        ON CONFLICT(name) DO UPDATE SET updated_at = excluded.updated_at
                        """,
                        (project, now),
                    )
                    conn.execute(
                        """
                        INSERT INTO standards_files (
                            project, relative_path, kind, title, description,
                            frontmatter, body, is_executable, version,
                            updated_at, updated_by
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            project,
                            relative_path,
                            kind,
                            title,
                            description,
                            frontmatter,
                            body,
                            int(is_executable),
                            now,
                            updated_by,
                        ),
                    )
                    new_version = 1
                else:
                    current_version = existing["version"]
                    if expected_version != current_version:
                        raise VersionConflict(
                            project, relative_path, expected_version or 0, current_version
                        )
                    conn.execute(
                        """
                        UPDATE standards_files
                        SET kind = ?, title = ?, description = ?, frontmatter = ?,
                            body = ?, is_executable = ?, version = version + 1,
                            updated_at = ?, updated_by = ?
                        WHERE project = ? AND relative_path = ?
                        """,
                        (
                            kind,
                            title,
                            description,
                            frontmatter,
                            body,
                            int(is_executable),
                            now,
                            updated_by,
                            project,
                            relative_path,
                        ),
                    )
                    conn.execute(
                        "UPDATE standards_projects SET updated_at = ? WHERE name = ?",
                        (now, project),
                    )
                    new_version = current_version + 1
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        row = self._get_file_sync(project, relative_path)
        assert row is not None
        assert row.version == new_version
        return row

    async def create_project_from_template(
        self,
        *,
        project: str,
        template_id: str,
        template_version: str,
        language: str,
        language_version: str,
        documents: list[dict],
        packs: list[dict] | None = None,
        updated_by: str | None = None,
    ) -> int:
        """Create a project and all of its documents in one transaction.

        `documents` entries carry the keys produced by the scaffold service:
        relative_path, kind, title, description, frontmatter, body,
        is_executable, source_hash, source_rule_ids, source_pack.

        `packs` records every pack that took part; template_id and language hold
        the first selected language, for display.

        Raises ProjectExists if the project is already there - scaffolding never
        merges into or overwrites an existing project.
        """
        return await asyncio.to_thread(
            self._create_project_from_template_sync,
            project,
            template_id,
            template_version,
            language,
            language_version,
            documents,
            packs or [],
            updated_by,
        )

    def _create_project_from_template_sync(
        self,
        project: str,
        template_id: str,
        template_version: str,
        language: str,
        language_version: str,
        documents: list[dict],
        packs: list[dict],
        updated_by: str | None,
    ) -> int:
        now = int(time.time())
        with _connect(self._path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                exists = conn.execute(
                    "SELECT 1 FROM standards_projects WHERE name = ?", (project,)
                ).fetchone()
                if exists is not None:
                    raise ProjectExists(project)

                conn.execute(
                    """
                    INSERT INTO standards_projects (
                        name, indicator, updated_at, template_id, template_version,
                        language, language_version, packs
                    ) VALUES (?, 'green', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project,
                        now,
                        template_id,
                        template_version,
                        language,
                        language_version,
                        json.dumps(packs),
                    ),
                )

                for doc in documents:
                    conn.execute(
                        """
                        INSERT INTO standards_files (
                            project, relative_path, kind, title, description,
                            frontmatter, body, is_executable, version,
                            updated_at, updated_by,
                            source_template, source_version, source_hash, source_rule_ids,
                            source_pack
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            project,
                            doc["relative_path"],
                            doc["kind"],
                            doc["title"],
                            doc.get("description", ""),
                            doc.get("frontmatter", ""),
                            doc["body"],
                            int(bool(doc.get("is_executable", False))),
                            now,
                            updated_by,
                            template_id,
                            template_version,
                            doc.get("source_hash", ""),
                            json.dumps(doc.get("source_rule_ids", [])),
                            doc.get("source_pack", ""),
                        ),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        logger.info(
            "Scaffolded project %s from template %s@%s (%d docs)",
            project,
            template_id,
            template_version,
            len(documents),
        )
        return len(documents)

    async def delete_project(self, project: str) -> int:
        """Delete a project and every document in it. Returns the document count.

        Irreversible: there is no soft-delete or trash. Callers are expected to
        confirm with the user first.
        """
        return await asyncio.to_thread(self._delete_project_sync, project)

    def _delete_project_sync(self, project: str) -> int:
        with _connect(self._path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                exists = conn.execute(
                    "SELECT 1 FROM standards_projects WHERE name = ?", (project,)
                ).fetchone()
                if exists is None:
                    conn.execute("ROLLBACK")
                    return 0
                # Delete rows explicitly rather than relying on the cascade: a DB
                # created before the foreign key existed would silently keep them.
                count = conn.execute(
                    "DELETE FROM standards_files WHERE project = ?", (project,)
                ).rowcount
                conn.execute("DELETE FROM standards_projects WHERE name = ?", (project,))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        logger.info("Deleted project %s (%d documents)", project, count)
        return count

    async def delete_file(self, project: str, relative_path: str) -> bool:
        return await asyncio.to_thread(self._delete_file_sync, project, relative_path)

    def _delete_file_sync(self, project: str, relative_path: str) -> bool:
        with _connect(self._path) as conn:
            cur = conn.execute(
                "DELETE FROM standards_files WHERE project = ? AND relative_path = ?",
                (project, relative_path),
            )
            deleted = cur.rowcount > 0
            if deleted:
                remaining = conn.execute(
                    "SELECT COUNT(*) AS n FROM standards_files WHERE project = ?",
                    (project,),
                ).fetchone()["n"]
                if remaining == 0:
                    conn.execute("DELETE FROM standards_projects WHERE name = ?", (project,))
                else:
                    conn.execute(
                        "UPDATE standards_projects SET updated_at = ? WHERE name = ?",
                        (int(time.time()), project),
                    )
        return deleted

    # -- seed / dump ----------------------------------------------------------

    async def seed_from_json(self, path: Path, *, force: bool = False) -> int:
        return await asyncio.to_thread(self._seed_from_json_sync, path, force)

    def _seed_from_json_sync(self, path: Path, force: bool) -> int:
        with _connect(self._path) as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM standards_files").fetchone()["n"]
        if count > 0 and not force:
            logger.info("Standards store already seeded (%d files); skipping", count)
            return 0

        if not path.is_file():
            logger.warning("Standards seed file not found at %s; nothing to seed", path)
            return 0

        entries = json.loads(path.read_text(encoding="utf-8"))
        now = int(time.time())
        loaded = 0
        with _connect(self._path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if force:
                    conn.execute("DELETE FROM standards_files")
                    conn.execute("DELETE FROM standards_projects")
                projects_seen: set[str] = set()
                for entry in entries:
                    project = entry["project"]
                    if project not in projects_seen:
                        conn.execute(
                            """
                            INSERT INTO standards_projects (name, indicator, updated_at)
                            VALUES (?, 'green', ?)
                            ON CONFLICT(name) DO NOTHING
                            """,
                            (project, now),
                        )
                        projects_seen.add(project)
                    from standards_scanner import (
                        _extract_description,
                        _extract_title,
                        _parse_frontmatter,
                    )

                    content = entry["content"]
                    kind = entry.get("kind", "markdown")
                    is_executable = bool(entry.get("is_executable", False))
                    relative_path = entry["relative_path"]

                    if kind == "markdown":
                        meta, body = _parse_frontmatter(content)
                        title = _extract_title(meta, body, relative_path.rsplit("/", 1)[-1])
                        description = _extract_description(meta)
                        frontmatter = _frontmatter_text(content)
                    else:
                        meta, body = {}, content
                        title = relative_path.rsplit("/", 1)[-1]
                        description = ""
                        frontmatter = ""

                    conn.execute(
                        """
                        INSERT INTO standards_files (
                            project, relative_path, kind, title, description,
                            frontmatter, body, is_executable, version,
                            updated_at, updated_by
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL)
                        ON CONFLICT(project, relative_path) DO NOTHING
                        """,
                        (
                            project,
                            relative_path,
                            kind,
                            title,
                            description,
                            frontmatter,
                            body,
                            int(is_executable),
                            now,
                        ),
                    )
                    loaded += 1
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        logger.info("Seeded %d standards files from %s", loaded, path)
        return loaded

    async def dump_to_dir(self, target: Path) -> int:
        """Dev-only convenience: write every file row back out to disk under
        `target/<project>/<relative_path>` for `rg` / `grep` / read-only
        inspection. Not exposed via HTTP."""
        return await asyncio.to_thread(self._dump_to_dir_sync, target)

    def _dump_to_dir_sync(self, target: Path) -> int:
        with _connect(self._path) as conn:
            rows = conn.execute(
                "SELECT project, relative_path, kind, frontmatter, body FROM standards_files"
            ).fetchall()
        count = 0
        for r in rows:
            out_path = target / r["project"] / r["relative_path"]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            content = r["body"]
            if r["kind"] == "markdown" and r["frontmatter"]:
                content = f"---\n{r['frontmatter']}\n---\n{r['body']}"
            out_path.write_text(content, encoding="utf-8")
            count += 1
        return count


def _frontmatter_text(content: str) -> str:
    """Extract the raw YAML text between the --- markers, without parsing it."""
    import re

    m = re.match(r"^---\s*\n(.*?\n)---\s*\n", content, re.DOTALL)
    return m.group(1).rstrip("\n") if m else ""


# ---------------------------------------------------------------------------
# CLI: `uv run -m standards_store seed [--force] [--seed-file PATH] [--db PATH]`
# ---------------------------------------------------------------------------


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="standards_store")
    sub = parser.add_subparsers(dest="command", required=True)

    seed_p = sub.add_parser("seed", help="Load the JSON seed file into the store")
    seed_p.add_argument("--force", action="store_true", help="Wipe and reload even if populated")
    seed_p.add_argument(
        "--seed-file",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "standards_seed.json",
    )
    seed_p.add_argument(
        "--db", type=Path, default=Path(__file__).resolve().parent / "data" / "metrics.db"
    )

    dump_p = sub.add_parser("dump", help="Write DB content back to a directory tree")
    dump_p.add_argument("target", type=Path)
    dump_p.add_argument(
        "--db", type=Path, default=Path(__file__).resolve().parent / "data" / "metrics.db"
    )

    args = parser.parse_args()

    async def run() -> None:
        if args.command == "seed":
            store = StandardsStore(args.db)
            await store.init()
            n = await store.seed_from_json(args.seed_file, force=args.force)
            print(f"Loaded {n} standards files into {args.db}")
        elif args.command == "dump":
            store = StandardsStore(args.db)
            await store.init()
            n = await store.dump_to_dir(args.target)
            print(f"Dumped {n} standards files to {args.target}")

    asyncio.run(run())


if __name__ == "__main__":
    _cli()


__all__ = [
    "StandardsStore",
    "ProjectRow",
    "FileRow",
    "ProjectExists",
    "VersionConflict",
]
