"""Tests for the dashboard routes (Starlette TestClient)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
async def app_with_data(tmp_path: Path):
    """Build the Starlette app with metrics seeded with a small dataset."""
    if "server" in sys.modules:
        del sys.modules["server"]
    server = importlib.import_module("server")

    metrics = server.MetricsStore(tmp_path / "metrics.db")
    await metrics.init()

    # Seed: alice (active), bob (never-called), charlie (inactive - old call)
    await metrics.upsert_registration(
        user_id="u1",
        user_name="alice",
        editor_name="claude-code",
        editor_version="1.2.3",
    )
    await metrics.record_call(
        user_id="u1",
        user_name="alice",
        editor_name="claude-code",
        tool_name="list_projects",
        args_summary="",
        latency_ms=4,
        status="ok",
    )
    await metrics.record_call(
        user_id="u1",
        user_name="alice",
        editor_name="claude-code",
        tool_name="search_rules",
        args_summary="query='dlq'",
        latency_ms=22,
        status="ok",
        query="dlq",
        top_result_path="proj-a/error-conventions.md",
        top_result_score=3.1,
    )
    await metrics.record_call(
        user_id="u1",
        user_name="alice",
        editor_name="claude-code",
        tool_name="search_rules",
        args_summary="query='nope'",
        latency_ms=15,
        status="empty",
        query="nope",
    )
    await metrics.record_call(
        user_id="u1",
        user_name="alice",
        editor_name="claude-code",
        tool_name="get_pattern",
        args_summary="project='proj-a', pattern='foo'",
        doc_path="proj-a/patterns/foo.md",
        latency_ms=7,
        status="ok",
    )

    await metrics.upsert_registration(
        user_id="u2",
        user_name="bob",
        editor_name="cursor",
        editor_version="0.42",
    )

    from auth import AuthStore

    auth_store = AuthStore(tmp_path / "auth.db")
    await auth_store.init()
    await auth_store.seed_default_admin("admin", "admin")

    cfg = server.McpConfig(auth_enabled=False)
    app = server.build_app(
        server.AppDeps(cfg=cfg, metrics=metrics, inactive_days=2, auth_store=auth_store),
    )
    return app, server


def _client(app):
    from starlette.testclient import TestClient

    return TestClient(app)


async def test_users_page_lists_all_statuses(app_with_data) -> None:
    app, _ = app_with_data
    r = _client(app).get("/dashboard/", headers={"X-MCP-User": "viewer"})
    assert r.status_code == 200
    body = r.text
    assert "alice" in body
    assert "bob" in body
    assert "active" in body
    assert "never called" in body


async def test_users_summary_counts(app_with_data) -> None:
    app, _ = app_with_data
    body = _client(app).get("/dashboard/", headers={"X-MCP-User": "v"}).text
    # Adoption KPI card renders the configured count and status labels.
    assert ">2<" in body  # total configured count
    assert "inactive" in body  # KPI label "X inactive"


async def test_tools_page_shows_call_counts(app_with_data) -> None:
    app, _ = app_with_data
    r = _client(app).get("/dashboard/tools", headers={"X-MCP-User": "v"})
    assert r.status_code == 200
    body = r.text
    # Aggregates fold historical names onto the current playbook_* names.
    assert "playbook_find" in body
    assert "playbook_get" in body
    assert "patterns/foo.md" in body  # doc fetches table


async def test_tools_window_param_changes_label(app_with_data) -> None:
    app, _ = app_with_data
    r = _client(app).get("/dashboard/tools?days=30", headers={"X-MCP-User": "v"})
    assert r.status_code == 200
    assert "last 30d" in r.text


async def test_searches_page_separates_zero_results(app_with_data) -> None:
    app, _ = app_with_data
    body = _client(app).get("/dashboard/searches", headers={"X-MCP-User": "v"}).text
    assert "dlq" in body
    assert "nope" in body
    # The zero-result heading is present.
    assert "Zero-result" in body


async def test_activity_page_shows_recent_calls(app_with_data) -> None:
    app, _ = app_with_data
    body = _client(app).get("/dashboard/activity", headers={"X-MCP-User": "v"}).text
    assert "list_projects" in body
    assert "search_rules" in body
    assert "claude-code" in body


async def test_user_detail_for_known_user(app_with_data) -> None:
    app, _ = app_with_data
    r = _client(app).get("/dashboard/users/alice", headers={"X-MCP-User": "v"})
    assert r.status_code == 200
    body = r.text
    assert "alice" in body
    assert "claude-code" in body


async def test_user_detail_404_for_unknown(app_with_data) -> None:
    app, _ = app_with_data
    r = _client(app).get("/dashboard/users/ghost", headers={"X-MCP-User": "v"})
    assert r.status_code == 404


async def test_root_dashboard_redirect(app_with_data) -> None:
    app, _ = app_with_data
    r = _client(app).get(
        "/dashboard",
        headers={"X-MCP-User": "v"},
        follow_redirects=False,
    )
    assert r.status_code == 307
    assert r.headers["location"].endswith("/dashboard/")


async def test_static_css_is_served(app_with_data) -> None:
    app, _ = app_with_data
    r = _client(app).get("/dashboard/static/style.css", headers={"X-MCP-User": "v"})
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]


async def test_healthz(app_with_data) -> None:
    app, _ = app_with_data
    r = _client(app).get("/healthz", headers={"X-MCP-User": "v"})
    assert r.status_code == 200
    assert r.text == "ok"


async def test_setup_page_returns_200(app_with_data) -> None:
    app, _ = app_with_data
    r = _client(app).get("/dashboard/setup", headers={"X-MCP-User": "v"})
    assert r.status_code == 200


async def test_setup_page_shows_sse_url(app_with_data) -> None:
    app, _ = app_with_data
    body = _client(app).get("/dashboard/setup", headers={"X-MCP-User": "v"}).text
    assert "/sse" in body


async def test_setup_page_shows_editor_sections(app_with_data) -> None:
    app, _ = app_with_data
    body = _client(app).get("/dashboard/setup", headers={"X-MCP-User": "v"}).text
    assert "Claude Code" in body
    assert "Cursor" in body
    assert "Windsurf" in body


async def test_setup_page_nav_link_present(app_with_data) -> None:
    app, _ = app_with_data
    body = _client(app).get("/dashboard/", headers={"X-MCP-User": "v"}).text
    assert "/dashboard/setup" in body


async def test_setup_status_pill_active_for_user_with_calls(app_with_data) -> None:
    app, _ = app_with_data
    body = _client(app).get("/dashboard/setup", headers={"X-MCP-User": "alice"}).text
    assert 'id="connect-status"' in body
    assert "is-active" in body
    assert "Connected" in body


async def test_setup_status_pill_waiting_for_unknown_user(app_with_data) -> None:
    app, _ = app_with_data
    body = _client(app).get("/dashboard/setup", headers={"X-MCP-User": "bob"}).text
    assert 'id="connect-status"' in body
    assert "Waiting for first call" in body
    # bob is registered but has never called, so the pill is NOT active.
    assert "is-active" not in body.split('id="connect-status"')[1].split("</div>")[0]


async def test_last_call_api_returns_json(app_with_data) -> None:
    app, _ = app_with_data
    r = _client(app).get("/dashboard/api/me/last-call", headers={"X-MCP-User": "alice"})
    assert r.status_code == 200
    data = r.json()
    assert data["user"] == "alice"
    assert data["last_call"] is not None
