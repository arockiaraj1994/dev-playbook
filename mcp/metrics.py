"""
metrics.py - SQLite-backed storage for MCP usage metrics.

Tracks two things:

  1. Registrations: (user, editor) pairs that have completed an MCP `initialize`
     handshake. Used to surface "configured but inactive" users.
  2. Calls: every tool invocation, with latency, status, and any extracted
     query / doc-path so the dashboard can rank tools and rule docs.

We use the stdlib `sqlite3` driver and run all blocking calls via
`asyncio.to_thread`. This keeps deployment one less dependency than
`aiosqlite`, and SQLite is more than enough for the expected volume
(internal tool, dozens of users).

All SQL uses parameterized queries.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS registrations (
        user_id            TEXT NOT NULL,
        editor_name        TEXT NOT NULL,
        user_name          TEXT NOT NULL,
        editor_version     TEXT NOT NULL DEFAULT '',
        first_seen         TEXT NOT NULL,
        last_initialize_at TEXT NOT NULL,
        PRIMARY KEY (user_id, editor_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS calls (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id          TEXT NOT NULL,
        user_name        TEXT NOT NULL,
        editor_name      TEXT NOT NULL DEFAULT '',
        tool_name        TEXT NOT NULL,
        args_summary     TEXT NOT NULL DEFAULT '',
        query            TEXT,
        doc_path         TEXT,
        top_result_path  TEXT,
        top_result_score REAL,
        latency_ms       INTEGER NOT NULL,
        status           TEXT NOT NULL,
        created_at       TEXT NOT NULL,
        -- Legacy columns from the two-corpus era (removed in v0.9.0). Kept so
        -- rows recorded before the removal still read back.
        requirement_id   TEXT,
        corpus           TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_calls_user ON calls(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_calls_tool ON calls(tool_name, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_calls_doc ON calls(doc_path, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_calls_created ON calls(created_at DESC)",
    # idx_calls_req is created AFTER the requirement_id migration below -
    # it must not live in _SCHEMA or upgrades of pre-0.6 DBs fail here.
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserRow:
    user_id: str
    user_name: str
    editors: str  # comma-separated "name version"
    first_seen: str
    last_seen: str | None  # last call (None if never called)
    calls_24h: int
    calls_7d: int
    status: str  # "active" | "inactive" | "never-called"


@dataclass(frozen=True)
class ToolStat:
    tool_name: str
    calls: int
    p50_latency_ms: float
    p95_latency_ms: float
    error_count: int


@dataclass(frozen=True)
class DocFetch:
    doc_path: str
    fetches: int
    last_fetched: str | None


@dataclass(frozen=True)
class SearchRow:
    query: str
    user_name: str
    top_result_path: str | None
    top_result_score: float | None
    project_filter: str | None
    created_at: str


@dataclass(frozen=True)
class CallRow:
    created_at: str
    user_name: str
    editor_name: str
    tool_name: str
    args_summary: str
    latency_ms: int
    status: str


@dataclass(frozen=True)
class UserDetail:
    user_id: str
    user_name: str
    editors: list[tuple[str, str, str]]  # (editor, version, last_initialize_at)
    first_seen: str
    last_seen: str | None
    calls_total: int
    calls_24h: int
    calls_7d: int
    status: str
    top_tools: list[tuple[str, int]]
    recent_calls: list[CallRow]


@dataclass(frozen=True)
class DashboardSummary:
    connected: int
    calls_today: int
    calls_trend: float
    zero_today: int
    zero_trend: int
    configured: int
    active: int
    inactive: int
    never_called: int
    hourly: list[dict]  # main-chart buckets: {label, ok, err, total}
    daily: list[dict]  # 7 items:  {label, search, get, list}
    live_users: list[dict]  # {user_name, editor, calls_24h, last_call}
    top_tools: list[dict]  # {tool, calls, share}
    window_days: int = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Historical tool names collapse onto the current playbook_* names so the
# dashboard keeps showing one row per tool across every rename. Unlike the
# frontmatter grammar - where v0.8.0 is a deliberate clean break - recorded
# calls are history and must not be orphaned, so this map only ever grows.
_LEGACY_TOOL_MAP = {
    # → playbook_start
    "start_task": "playbook_start",
    "playbook_start_task": "playbook_start",
    "start_requirement": "playbook_start",
    "playbook_start_requirement": "playbook_start",
    # → playbook_get
    "get_doc": "playbook_get",
    "playbook_get_doc": "playbook_get",
    "get_requirement": "playbook_get",
    "get_guardrails": "playbook_get",
    "get_agents_md": "playbook_get",
    "get_architecture": "playbook_get",
    "get_language_rules": "playbook_get",
    "get_pattern": "playbook_get",
    "get_skill": "playbook_get",
    "get_workflow": "playbook_get",
    "get_gate": "playbook_get",
    # → playbook_find
    "find_rules": "playbook_find",
    "search_rules": "playbook_find",
    "list_rule_docs": "playbook_find",
    "get_index": "playbook_find",
    "playbook_search_docs": "playbook_find",
    "list_requirements": "playbook_find",
    "playbook_list_requirements": "playbook_find",
}

_CANONICAL_TOOL_SQL = (
    "CASE tool_name "
    + " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in _LEGACY_TOOL_MAP.items())
    + " ELSE tool_name END"
)

# Family classification on the canonical name (search / get / start) for the
# by-tool-family breakdown. LIKE fallbacks keep truly unknown names counted.
_FAMILY_SEARCH_SQL = f"({_CANONICAL_TOOL_SQL}) = 'playbook_find'"
_FAMILY_GET_SQL = f"({_CANONICAL_TOOL_SQL}) = 'playbook_get'"
_FAMILY_START_SQL = f"({_CANONICAL_TOOL_SQL}) = 'playbook_start' OR tool_name LIKE 'start%'"


def _now() -> str:
    """ISO8601 UTC timestamp used as TEXT in SQLite."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _iso_offset(*, days: int = 0, hours: int = 0, minutes: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=days, hours=hours, minutes=minutes)).isoformat(
        timespec="seconds"
    )


def _percentile(values: list[int], p: float) -> float:
    """Linear-interpolated percentile (no numpy)."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    if lo == hi:
        return float(s[lo])
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


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


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class MetricsStore:
    """Thread-safe-via-asyncio.to_thread wrapper over a SQLite file."""

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)

    @property
    def path(self) -> Path:
        return self._path

    # -- lifecycle ----------------------------------------------------------

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)
        logger.info("Metrics DB initialized at %s", self._path)

    def _init_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._path) as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            # Migrate: add requirement_id / corpus if upgrading an older DB.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(calls)").fetchall()}
            if "requirement_id" not in cols:
                conn.execute("ALTER TABLE calls ADD COLUMN requirement_id TEXT")
            if "corpus" not in cols:
                conn.execute("ALTER TABLE calls ADD COLUMN corpus TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_calls_req ON calls(requirement_id, created_at DESC)"
            )

    # -- writers ------------------------------------------------------------

    async def upsert_registration(
        self,
        *,
        user_id: str,
        user_name: str,
        editor_name: str,
        editor_version: str,
    ) -> None:
        await asyncio.to_thread(
            self._upsert_registration_sync,
            user_id,
            user_name,
            editor_name,
            editor_version,
        )

    def _upsert_registration_sync(
        self,
        user_id: str,
        user_name: str,
        editor_name: str,
        editor_version: str,
    ) -> None:
        now = _now()
        with _connect(self._path) as conn:
            conn.execute(
                """
                INSERT INTO registrations
                  (user_id, user_name, editor_name, editor_version,
                   first_seen, last_initialize_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, editor_name) DO UPDATE SET
                  user_name = excluded.user_name,
                  editor_version = excluded.editor_version,
                  last_initialize_at = excluded.last_initialize_at
                """,
                (user_id, user_name, editor_name, editor_version, now, now),
            )

    async def record_call(
        self,
        *,
        user_id: str,
        user_name: str,
        editor_name: str,
        tool_name: str,
        args_summary: str,
        latency_ms: int,
        status: str,
        query: str | None = None,
        doc_path: str | None = None,
        top_result_path: str | None = None,
        top_result_score: float | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._record_call_sync,
            user_id,
            user_name,
            editor_name,
            tool_name,
            args_summary,
            latency_ms,
            status,
            query,
            doc_path,
            top_result_path,
            top_result_score,
        )

    def _record_call_sync(
        self,
        user_id: str,
        user_name: str,
        editor_name: str,
        tool_name: str,
        args_summary: str,
        latency_ms: int,
        status: str,
        query: str | None,
        doc_path: str | None,
        top_result_path: str | None,
        top_result_score: float | None,
    ) -> None:
        with _connect(self._path) as conn:
            conn.execute(
                """
                INSERT INTO calls (
                    user_id, user_name, editor_name, tool_name, args_summary,
                    query, doc_path, top_result_path, top_result_score,
                    latency_ms, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    user_name,
                    editor_name,
                    tool_name,
                    args_summary,
                    query,
                    doc_path,
                    top_result_path,
                    top_result_score,
                    latency_ms,
                    status,
                    _now(),
                ),
            )

    # -- readers ------------------------------------------------------------

    async def list_users(self, *, inactive_days: int) -> list[UserRow]:
        return await asyncio.to_thread(self._list_users_sync, inactive_days)

    def _list_users_sync(self, inactive_days: int) -> list[UserRow]:
        threshold = _iso_offset(days=inactive_days)
        cutoff_24h = _iso_offset(hours=24)
        cutoff_7d = _iso_offset(days=7)
        with _connect(self._path) as conn:
            rows = conn.execute(
                """
                SELECT
                    r.user_id     AS user_id,
                    r.user_name   AS user_name,
                    GROUP_CONCAT(r.editor_name || ' ' || r.editor_version, ', ')
                                  AS editors,
                    MIN(r.first_seen) AS first_seen,
                    (SELECT MAX(c.created_at) FROM calls c
                       WHERE c.user_id = r.user_id) AS last_seen,
                    (SELECT COUNT(*) FROM calls c
                       WHERE c.user_id = r.user_id AND c.created_at >= ?)
                                  AS calls_24h,
                    (SELECT COUNT(*) FROM calls c
                       WHERE c.user_id = r.user_id AND c.created_at >= ?)
                                  AS calls_7d
                FROM registrations r
                GROUP BY r.user_id, r.user_name
                ORDER BY last_seen DESC NULLS LAST, r.user_name
                """,
                (cutoff_24h, cutoff_7d),
            ).fetchall()

        out: list[UserRow] = []
        for r in rows:
            last_seen = r["last_seen"]
            if last_seen is None:
                status = "never-called"
            elif last_seen >= threshold:
                status = "active"
            else:
                status = "inactive"
            out.append(
                UserRow(
                    user_id=r["user_id"],
                    user_name=r["user_name"],
                    editors=r["editors"] or "",
                    first_seen=r["first_seen"],
                    last_seen=last_seen,
                    calls_24h=r["calls_24h"],
                    calls_7d=r["calls_7d"],
                    status=status,
                )
            )
        return out

    async def adoption_summary(self, *, inactive_days: int) -> dict[str, int]:
        users = await self.list_users(inactive_days=inactive_days)
        summary = {"total": len(users), "active": 0, "inactive": 0, "never_called": 0}
        for u in users:
            if u.status == "active":
                summary["active"] += 1
            elif u.status == "inactive":
                summary["inactive"] += 1
            else:
                summary["never_called"] += 1
        return summary

    async def list_tool_stats(self, *, window_days: int) -> list[ToolStat]:
        return await asyncio.to_thread(self._list_tool_stats_sync, window_days)

    def _list_tool_stats_sync(self, window_days: int) -> list[ToolStat]:
        cutoff = _iso_offset(days=window_days)
        with _connect(self._path) as conn:
            rows = conn.execute(
                """
                SELECT tool_name,
                       latency_ms,
                       status
                FROM calls
                WHERE created_at >= ?
                """,
                (cutoff,),
            ).fetchall()
        by_tool: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            canonical = _LEGACY_TOOL_MAP.get(r["tool_name"], r["tool_name"])
            by_tool.setdefault(canonical, []).append(r)
        out: list[ToolStat] = []
        for tool_name, tool_rows in by_tool.items():
            latencies = [r["latency_ms"] for r in tool_rows]
            errors = sum(1 for r in tool_rows if r["status"] != "ok")
            out.append(
                ToolStat(
                    tool_name=tool_name,
                    calls=len(tool_rows),
                    p50_latency_ms=round(_percentile(latencies, 0.5), 1),
                    p95_latency_ms=round(_percentile(latencies, 0.95), 1),
                    error_count=errors,
                )
            )
        out.sort(key=lambda t: t.calls, reverse=True)
        return out

    async def list_doc_fetches(self, *, window_days: int, limit: int) -> list[DocFetch]:
        return await asyncio.to_thread(self._list_doc_fetches_sync, window_days, limit)

    def _list_doc_fetches_sync(self, window_days: int, limit: int) -> list[DocFetch]:
        cutoff = _iso_offset(days=window_days)
        with _connect(self._path) as conn:
            rows = conn.execute(
                """
                SELECT doc_path,
                       COUNT(*)        AS fetches,
                       MAX(created_at) AS last_fetched
                FROM calls
                WHERE doc_path IS NOT NULL
                  AND created_at >= ?
                GROUP BY doc_path
                ORDER BY fetches DESC, last_fetched DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        return [
            DocFetch(
                doc_path=r["doc_path"],
                fetches=r["fetches"],
                last_fetched=r["last_fetched"],
            )
            for r in rows
        ]

    async def list_searches(self, *, limit: int) -> list[SearchRow]:
        return await asyncio.to_thread(self._list_searches_sync, limit, False)

    async def list_zero_result_searches(self, *, limit: int) -> list[SearchRow]:
        return await asyncio.to_thread(self._list_searches_sync, limit, True)

    def _list_searches_sync(self, limit: int, zero_only: bool) -> list[SearchRow]:
        clause = "AND (top_result_path IS NULL OR status = 'empty')" if zero_only else ""
        sql = f"""
            SELECT query, user_name, top_result_path, top_result_score,
                   args_summary, created_at
            FROM calls
            WHERE tool_name IN ('search_rules', 'find_rules', 'playbook_search_docs')
              AND query IS NOT NULL {clause}
            ORDER BY created_at DESC
            LIMIT ?
        """
        with _connect(self._path) as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [
            SearchRow(
                query=r["query"],
                user_name=r["user_name"],
                top_result_path=r["top_result_path"],
                top_result_score=r["top_result_score"],
                project_filter=_extract_project_from_args(r["args_summary"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def dashboard_summary(
        self, *, inactive_days: int, window_days: int = 1
    ) -> DashboardSummary:
        return await asyncio.to_thread(self._dashboard_summary_sync, inactive_days, window_days)

    def _dashboard_summary_sync(self, inactive_days: int, window_days: int = 1) -> DashboardSummary:
        window_days = window_days if window_days in (1, 7, 30) else 1
        cutoff_window = _iso_offset(days=window_days)
        cutoff_prev_window = _iso_offset(days=window_days * 2)
        cutoff_24h = _iso_offset(hours=24)
        cutoff_7d = _iso_offset(days=7)
        # "Connected" = initialized within the last 4 hours (SSE connections
        # are long-lived; initialize fires once when the editor connects).
        cutoff_connected = _iso_offset(hours=4)

        with _connect(self._path) as conn:
            # Calls in this window and the previous one (for trend)
            r = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS calls_today,
                    SUM(CASE WHEN {_FAMILY_SEARCH_SQL}
                              AND (top_result_path IS NULL OR status = 'empty')
                             THEN 1 ELSE 0 END) AS zero_today
                FROM calls WHERE created_at >= ?
                """,
                (cutoff_window,),
            ).fetchone()
            calls_today: int = r["calls_today"] or 0
            zero_today: int = r["zero_today"] or 0

            r2 = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS calls_yesterday,
                    SUM(CASE WHEN {_FAMILY_SEARCH_SQL}
                              AND (top_result_path IS NULL OR status = 'empty')
                             THEN 1 ELSE 0 END) AS zero_yesterday
                FROM calls
                WHERE created_at >= ? AND created_at < ?
                """,
                (cutoff_prev_window, cutoff_window),
            ).fetchone()
            calls_yesterday: int = r2["calls_yesterday"] or 0
            zero_yesterday: int = r2["zero_yesterday"] or 0

            # Connected = distinct editors that sent `initialize` in last 4h.
            # SSE connections are long-lived; initialize fires once at connect
            # time, so last_initialize_at is the correct "online" signal.
            live_rows = conn.execute(
                """
                SELECT
                    r.user_name,
                    r.editor_name,
                    r.last_initialize_at AS last_call,
                    COALESCE((
                        SELECT COUNT(*) FROM calls c
                        WHERE c.user_id = r.user_id AND c.created_at >= ?
                    ), 0) AS calls_24h
                FROM registrations r
                WHERE r.last_initialize_at >= ?
                ORDER BY r.last_initialize_at DESC
                LIMIT 10
                """,
                (cutoff_24h, cutoff_connected),
            ).fetchall()

            # Main-chart buckets: hourly for the 24h window, daily otherwise
            if window_days == 1:
                bucket_rows = conn.execute(
                    """
                    SELECT
                        CAST(strftime('%H', created_at) AS INTEGER) AS bucket,
                        SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END)  AS ok_count,
                        SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS err_count
                    FROM calls
                    WHERE created_at >= ?
                    GROUP BY bucket
                    ORDER BY bucket
                    """,
                    (cutoff_24h,),
                ).fetchall()
            else:
                bucket_rows = conn.execute(
                    """
                    SELECT
                        date(created_at) AS bucket,
                        SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END)  AS ok_count,
                        SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS err_count
                    FROM calls
                    WHERE created_at >= ?
                    GROUP BY bucket
                    ORDER BY bucket
                    """,
                    (cutoff_window,),
                ).fetchall()

            # 7-day daily breakdown by tool family (canonical names)
            daily_rows = conn.execute(
                f"""
                SELECT
                    date(created_at) AS day,
                    SUM(CASE WHEN {_FAMILY_SEARCH_SQL} THEN 1 ELSE 0 END) AS search_count,
                    SUM(CASE WHEN {_FAMILY_GET_SQL}    THEN 1 ELSE 0 END) AS get_count,
                    SUM(CASE WHEN {_FAMILY_START_SQL}  THEN 1 ELSE 0 END) AS start_count
                FROM calls
                WHERE created_at >= ?
                GROUP BY date(created_at)
                ORDER BY day
                """,
                (cutoff_7d,),
            ).fetchall()

            # Top tools for the window, legacy names folded onto playbook_*
            top_tool_rows = conn.execute(
                f"""
                SELECT {_CANONICAL_TOOL_SQL} AS tool, COUNT(*) AS n
                FROM calls WHERE created_at >= ?
                GROUP BY tool ORDER BY n DESC LIMIT 7
                """,
                (cutoff_window,),
            ).fetchall()

            # Adoption counts
            threshold = _iso_offset(days=inactive_days)
            adoption_rows = conn.execute(
                """
                SELECT
                    COUNT(DISTINCT r.user_id) AS total,
                    SUM(CASE WHEN
                            (SELECT MAX(c.created_at) FROM calls c
                             WHERE c.user_id = r.user_id) >= ?
                        THEN 1 ELSE 0 END) AS active,
                    SUM(CASE WHEN
                            (SELECT MAX(c.created_at) FROM calls c
                             WHERE c.user_id = r.user_id) IS NULL
                        THEN 1 ELSE 0 END) AS never_called
                FROM registrations r
                """,
                (threshold,),
            ).fetchone()
            total_users: int = adoption_rows["total"] or 0
            active_users: int = adoption_rows["active"] or 0
            never_called: int = adoption_rows["never_called"] or 0
            inactive_users: int = total_users - active_users - never_called

        # Build the filled main-chart series
        hourly: list[dict] = []
        if window_days == 1:
            by_hour: dict[int, tuple[int, int]] = {
                r["bucket"]: (r["ok_count"] or 0, r["err_count"] or 0) for r in bucket_rows
            }
            for h in range(24):
                ok, err = by_hour.get(h, (0, 0))
                hourly.append({"label": f"{h:02d}:00", "ok": ok, "err": err, "total": ok + err})
        else:
            by_day: dict[str, tuple[int, int]] = {
                r["bucket"]: (r["ok_count"] or 0, r["err_count"] or 0) for r in bucket_rows
            }
            today_for_series = datetime.now(UTC).date()
            for offset in range(window_days - 1, -1, -1):
                d = today_for_series - timedelta(days=offset)
                ok, err = by_day.get(str(d), (0, 0))
                hourly.append(
                    {"label": d.strftime("%d %b"), "ok": ok, "err": err, "total": ok + err}
                )

        # Build 7-day series (last 7 calendar days)
        daily_by_day: dict[str, dict] = {
            r["day"]: {
                "search": r["search_count"] or 0,
                "get": r["get_count"] or 0,
                "start": r["start_count"] or 0,
            }
            for r in daily_rows
        }
        today_dt = datetime.now(UTC).date()
        DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        daily: list[dict] = []
        for offset in range(6, -1, -1):
            d = today_dt - timedelta(days=offset)
            key = str(d)
            data = daily_by_day.get(key, {"search": 0, "get": 0, "start": 0})
            daily.append({"label": DAY_ABBR[d.weekday()], **data})

        # Live users list
        live_users = [
            {
                "user_name": row["user_name"],
                "editor": row["editor_name"] or " - ",
                "calls_24h": row["calls_24h"],
                "last_call": row["last_call"],
            }
            for row in live_rows
        ]

        # Top tools list
        top_tools = [{"tool": r["tool"], "calls": r["n"]} for r in top_tool_rows]
        max_calls = top_tools[0]["calls"] if top_tools else 1
        for t in top_tools:
            t["share"] = round(t["calls"] / max_calls, 3)

        # Trends
        def _trend(today: int, yesterday: int) -> float:
            if yesterday == 0:
                return 0.0
            return round((today - yesterday) / yesterday * 100, 1)

        return DashboardSummary(
            connected=len(live_users),
            calls_today=calls_today,
            calls_trend=_trend(calls_today, calls_yesterday),
            zero_today=zero_today,
            zero_trend=zero_today - zero_yesterday,
            configured=total_users,
            active=active_users,
            inactive=inactive_users,
            never_called=never_called,
            hourly=hourly,
            daily=daily,
            live_users=live_users,
            top_tools=top_tools,
            window_days=window_days,
        )

    async def list_recent_calls(self, *, limit: int) -> list[CallRow]:
        return await asyncio.to_thread(self._list_recent_calls_sync, limit)

    def _list_recent_calls_sync(self, limit: int) -> list[CallRow]:
        with _connect(self._path) as conn:
            rows = conn.execute(
                """
                SELECT created_at, user_name, editor_name, tool_name,
                       args_summary, latency_ms, status
                FROM calls
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            CallRow(
                created_at=r["created_at"],
                user_name=r["user_name"],
                editor_name=r["editor_name"],
                tool_name=r["tool_name"],
                args_summary=r["args_summary"],
                latency_ms=r["latency_ms"],
                status=r["status"],
            )
            for r in rows
        ]

    async def get_user(self, *, user_name: str, inactive_days: int) -> UserDetail | None:
        return await asyncio.to_thread(
            self._get_user_sync,
            user_name,
            inactive_days,
        )

    def _get_user_sync(self, user_name: str, inactive_days: int) -> UserDetail | None:
        with _connect(self._path) as conn:
            reg_rows = conn.execute(
                """
                SELECT user_id, user_name, editor_name, editor_version,
                       first_seen, last_initialize_at
                FROM registrations
                WHERE user_name = ?
                ORDER BY last_initialize_at DESC
                """,
                (user_name,),
            ).fetchall()
            if not reg_rows:
                return None

            user_id = reg_rows[0]["user_id"]
            first_seen = min(r["first_seen"] for r in reg_rows)
            editors = [
                (r["editor_name"], r["editor_version"], r["last_initialize_at"]) for r in reg_rows
            ]

            cutoff_24h = _iso_offset(hours=24)
            cutoff_7d = _iso_offset(days=7)
            stats = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS c24,
                    SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS c7,
                    MAX(created_at) AS last_seen
                FROM calls
                WHERE user_id = ?
                """,
                (cutoff_24h, cutoff_7d, user_id),
            ).fetchone()

            top_tools_rows = conn.execute(
                """
                SELECT tool_name, COUNT(*) AS n
                FROM calls
                WHERE user_id = ?
                GROUP BY tool_name
                ORDER BY n DESC
                LIMIT 10
                """,
                (user_id,),
            ).fetchall()

            recent_rows = conn.execute(
                """
                SELECT created_at, user_name, editor_name, tool_name,
                       args_summary, latency_ms, status
                FROM calls
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 25
                """,
                (user_id,),
            ).fetchall()

        last_seen = stats["last_seen"]
        threshold = _iso_offset(days=inactive_days)
        if last_seen is None:
            status = "never-called"
        elif last_seen >= threshold:
            status = "active"
        else:
            status = "inactive"

        return UserDetail(
            user_id=user_id,
            user_name=user_name,
            editors=editors,
            first_seen=first_seen,
            last_seen=last_seen,
            calls_total=int(stats["total"] or 0),
            calls_24h=int(stats["c24"] or 0),
            calls_7d=int(stats["c7"] or 0),
            status=status,
            top_tools=[(r["tool_name"], r["n"]) for r in top_tools_rows],
            recent_calls=[
                CallRow(
                    created_at=r["created_at"],
                    user_name=r["user_name"],
                    editor_name=r["editor_name"],
                    tool_name=r["tool_name"],
                    args_summary=r["args_summary"],
                    latency_ms=r["latency_ms"],
                    status=r["status"],
                )
                for r in recent_rows
            ],
        )


# ---------------------------------------------------------------------------
# Helpers used by readers
# ---------------------------------------------------------------------------


def _extract_project_from_args(args_summary: str) -> str | None:
    """args_summary is a short JSON-ish string; try to parse `project=...`."""
    if not args_summary:
        return None
    needle = "project="
    idx = args_summary.find(needle)
    if idx < 0:
        return None
    rest = args_summary[idx + len(needle) :]
    end = 0
    for ch in rest:
        if ch in (",", " ", "}", ")"):
            break
        end += 1
    return rest[:end].strip("'\"") or None


# ---------------------------------------------------------------------------
# Public helper for callers
# ---------------------------------------------------------------------------


def summarize_args(arguments: dict, max_len: int = 200) -> str:
    """Compact `key=value, key=value` summary suitable for logs/dashboard."""
    parts: list[str] = []
    for k, v in arguments.items():
        if isinstance(v, str):
            shown = v if len(v) <= 80 else v[:80] + "..."
            parts.append(f"{k}={shown!r}")
        else:
            parts.append(f"{k}={v!r}")
    out = ", ".join(parts)
    if len(out) > max_len:
        out = out[:max_len] + "..."
    return out


# Convenience re-export so consumers can import everything from one module.
__all__ = [
    "MetricsStore",
    "UserRow",
    "ToolStat",
    "DocFetch",
    "SearchRow",
    "CallRow",
    "UserDetail",
    "DashboardSummary",
    "summarize_args",
]
