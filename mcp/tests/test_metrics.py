"""Tests for metrics.py - storage, helpers, and aggregations."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from metrics import (
    MetricsStore,
    _percentile,
    summarize_args,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_percentile_empty() -> None:
    assert _percentile([], 0.5) == 0.0


def test_percentile_singleton() -> None:
    assert _percentile([42], 0.5) == 42.0
    assert _percentile([42], 0.95) == 42.0


def test_percentile_monotonic() -> None:
    xs = list(range(1, 101))  # 1..100
    p50 = _percentile(xs, 0.5)
    p95 = _percentile(xs, 0.95)
    assert 49 <= p50 <= 51
    assert 94 <= p95 <= 96


def test_summarize_args_basic() -> None:
    out = summarize_args({"project": "p", "context": "anti-patterns"})
    assert "project='p'" in out
    assert "context='anti-patterns'" in out


def test_summarize_args_truncates_long_string() -> None:
    out = summarize_args({"query": "x" * 200})
    assert out.startswith("query=")
    assert "..." in out
    assert len(out) <= 220


def test_summarize_args_handles_non_string_values() -> None:
    out = summarize_args({"top_k": 10, "flag": True})
    assert "top_k=10" in out
    assert "flag=True" in out


# ---------------------------------------------------------------------------
# MetricsStore
# ---------------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path: Path) -> MetricsStore:
    s = MetricsStore(tmp_path / "metrics.db")
    await s.init()
    return s


async def test_init_creates_tables(tmp_path: Path) -> None:
    s = MetricsStore(tmp_path / "m.db")
    await s.init()
    with sqlite3.connect(str(tmp_path / "m.db")) as conn:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert {"registrations", "calls"}.issubset(names)


async def test_upsert_registration_inserts_then_updates(store: MetricsStore) -> None:
    await store.upsert_registration(
        user_id="u1",
        user_name="alice",
        editor_name="cursor",
        editor_version="1.0",
    )
    await store.upsert_registration(
        user_id="u1",
        user_name="alice",
        editor_name="cursor",
        editor_version="1.1",
    )
    with sqlite3.connect(str(store.path)) as conn:
        rows = conn.execute("SELECT user_name, editor_version FROM registrations").fetchall()
    assert rows == [("alice", "1.1")]


async def test_record_call_writes_row(store: MetricsStore) -> None:
    await store.record_call(
        user_id="u1",
        user_name="alice",
        editor_name="cursor",
        tool_name="search_rules",
        args_summary="query='dlq'",
        latency_ms=12,
        status="ok",
        query="dlq",
        top_result_path="p/error-conventions.md",
        top_result_score=4.2,
    )
    with sqlite3.connect(str(store.path)) as conn:
        rows = conn.execute("SELECT tool_name, status, query FROM calls").fetchall()
    assert rows == [("search_rules", "ok", "dlq")]


async def test_list_users_status_classification(store: MetricsStore) -> None:
    # alice - recent call → active
    await store.upsert_registration(
        user_id="u1",
        user_name="alice",
        editor_name="claude-code",
        editor_version="1",
    )
    await store.record_call(
        user_id="u1",
        user_name="alice",
        editor_name="claude-code",
        tool_name="list_projects",
        args_summary="",
        latency_ms=5,
        status="ok",
    )
    # bob - registered but never called → never-called
    await store.upsert_registration(
        user_id="u2",
        user_name="bob",
        editor_name="cursor",
        editor_version="1",
    )
    # charlie - registered, called long ago → inactive
    await store.upsert_registration(
        user_id="u3",
        user_name="charlie",
        editor_name="cursor",
        editor_version="1",
    )
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat(timespec="seconds")
    with sqlite3.connect(str(store.path)) as conn:
        conn.execute(
            """INSERT INTO calls
               (user_id, user_name, editor_name, tool_name, args_summary,
                latency_ms, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("u3", "charlie", "cursor", "list_projects", "", 5, "ok", old),
        )

    users = await store.list_users(inactive_days=2)
    by_name = {u.user_name: u for u in users}
    assert by_name["alice"].status == "active"
    assert by_name["bob"].status == "never-called"
    assert by_name["charlie"].status == "inactive"


async def test_adoption_summary(store: MetricsStore) -> None:
    await store.upsert_registration(
        user_id="u1",
        user_name="a",
        editor_name="cursor",
        editor_version="1",
    )
    await store.record_call(
        user_id="u1",
        user_name="a",
        editor_name="cursor",
        tool_name="list_projects",
        args_summary="",
        latency_ms=1,
        status="ok",
    )
    await store.upsert_registration(
        user_id="u2",
        user_name="b",
        editor_name="cursor",
        editor_version="1",
    )
    summary = await store.adoption_summary(inactive_days=2)
    assert summary == {"total": 2, "active": 1, "inactive": 0, "never_called": 1}


async def test_list_tool_stats_aggregates(store: MetricsStore) -> None:
    common = dict(
        user_id="u1",
        user_name="alice",
        editor_name="cursor",
        args_summary="",
        status="ok",
    )
    for ms in [10, 20, 30, 40, 50]:
        await store.record_call(tool_name="search_rules", latency_ms=ms, **common)
    await store.record_call(
        tool_name="get_pattern", latency_ms=99, **{**common, "status": "not_found"}
    )
    stats = await store.list_tool_stats(window_days=7)
    # Historical names fold onto the current playbook_* names for display, so
    # no rename orphans already-recorded calls.
    by_tool = {s.tool_name: s for s in stats}
    assert by_tool["playbook_find_standards"].calls == 5
    assert by_tool["playbook_find_standards"].error_count == 0
    assert 25 <= by_tool["playbook_find_standards"].p50_latency_ms <= 35
    assert by_tool["playbook_get_standard"].error_count == 1


async def test_list_doc_fetches(store: MetricsStore) -> None:
    common = dict(
        user_id="u1",
        user_name="alice",
        editor_name="cursor",
        args_summary="",
        latency_ms=5,
        status="ok",
    )
    for _ in range(3):
        await store.record_call(tool_name="get_pattern", doc_path="p/patterns/react.md", **common)
    await store.record_call(tool_name="get_pattern", doc_path="p/patterns/quarkus.md", **common)
    fetches = await store.list_doc_fetches(window_days=7, limit=10)
    by_doc = {d.doc_path: d.fetches for d in fetches}
    assert by_doc["p/patterns/react.md"] == 3
    assert by_doc["p/patterns/quarkus.md"] == 1


async def test_list_searches_separates_zero_results(store: MetricsStore) -> None:
    common = dict(
        user_id="u1",
        user_name="alice",
        editor_name="cursor",
        tool_name="search_rules",
        args_summary="query='x'",
        latency_ms=5,
    )
    await store.record_call(
        query="found",
        top_result_path="p/x.md",
        top_result_score=2.0,
        status="ok",
        **common,
    )
    await store.record_call(
        query="missing",
        status="empty",
        **common,
    )
    recent = await store.list_searches(limit=10)
    zeros = await store.list_zero_result_searches(limit=10)
    assert {s.query for s in recent} == {"found", "missing"}
    assert [s.query for s in zeros] == ["missing"]


async def test_get_user_returns_none_for_unknown(store: MetricsStore) -> None:
    assert await store.get_user(user_name="nobody", inactive_days=2) is None


async def test_get_user_aggregates(store: MetricsStore) -> None:
    await store.upsert_registration(
        user_id="u1",
        user_name="alice",
        editor_name="cursor",
        editor_version="1.0",
    )
    await store.upsert_registration(
        user_id="u1",
        user_name="alice",
        editor_name="claude-code",
        editor_version="1.2",
    )
    for tool in ["list_projects", "list_projects", "get_pattern"]:
        await store.record_call(
            user_id="u1",
            user_name="alice",
            editor_name="cursor",
            tool_name=tool,
            args_summary="",
            latency_ms=5,
            status="ok",
        )
    detail = await store.get_user(user_name="alice", inactive_days=2)
    assert detail is not None
    assert detail.calls_total == 3
    assert detail.status == "active"
    assert {e[0] for e in detail.editors} == {"cursor", "claude-code"}
    top = dict(detail.top_tools)
    assert top["list_projects"] == 2
    assert top["get_pattern"] == 1
