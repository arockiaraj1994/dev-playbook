"""Tests for the standards dashboard routes (create/delete/detail/JSON edit API)."""

from __future__ import annotations

import base64
import importlib
import sys
from pathlib import Path

import pytest


async def _seed_agents(standards) -> None:
    await standards.upsert_file(
        project="nexre",
        relative_path="AGENTS.md",
        kind="markdown",
        title="Agents",
        description="Agent guide.",
        frontmatter="title: Agents\ndescription: Agent guide.",
        body="Body content that is definitely long enough to pass the minimum length rule check.",
        expected_version=None,
    )


@pytest.fixture
async def app_noauth(tmp_path: Path):
    """auth disabled: X-MCP-User header always resolves role='user' (never admin).

    Used for read-only rendering checks and for "non-admin gets 403" checks,
    matching the convention already used in test_dashboard.py.
    """
    if "server" in sys.modules:
        del sys.modules["server"]
    server = importlib.import_module("server")

    metrics = server.MetricsStore(tmp_path / "metrics.db")
    await metrics.init()

    from auth import AuthStore

    auth_store = AuthStore(tmp_path / "metrics.db")
    await auth_store.init()
    await auth_store.seed_default_admin("admin", "admin")

    from standards_store import StandardsStore

    standards = StandardsStore(tmp_path / "metrics.db")
    await standards.init()
    await _seed_agents(standards)

    cfg = server.McpConfig(auth_enabled=False)
    app = server.build_app(
        server.AppDeps(
            cfg=cfg, metrics=metrics, inactive_days=2, auth_store=auth_store, standards=standards
        ),
    )
    return app, standards


@pytest.fixture
async def app_admin(tmp_path: Path):
    """auth enabled: tests log in for real as the seeded admin via /login."""
    if "server" in sys.modules:
        del sys.modules["server"]
    server = importlib.import_module("server")

    metrics = server.MetricsStore(tmp_path / "metrics.db")
    await metrics.init()

    from auth import AuthStore

    auth_store = AuthStore(tmp_path / "metrics.db")
    await auth_store.init()
    await auth_store.seed_default_admin("admin", "adminpw")

    from standards_store import StandardsStore

    standards = StandardsStore(tmp_path / "metrics.db")
    await standards.init()
    await _seed_agents(standards)

    cfg = server.McpConfig(auth_enabled=True, admin_username="admin", admin_password="adminpw")
    app = server.build_app(
        server.AppDeps(
            cfg=cfg, metrics=metrics, inactive_days=2, auth_store=auth_store, standards=standards
        ),
    )
    return app, standards


def _client(app):
    from starlette.testclient import TestClient

    return TestClient(app)


def _login(client, username: str, password: str) -> str:
    """Log in via the real /login flow; returns the csrf token for reuse."""
    client.get("/login")
    csrf = client.cookies.get("csrf_token")
    resp = client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return csrf


# -- Read-only rendering ----------------------------------------------------


async def test_standards_list_shows_seeded_project(app_noauth) -> None:
    app, _ = app_noauth
    r = _client(app).get("/dashboard/standards", headers={"X-MCP-User": "v"})
    assert r.status_code == 200
    assert "nexre" in r.text


async def test_standard_detail_shows_frontmatter_and_tabs(app_noauth) -> None:
    app, _ = app_noauth
    r = _client(app).get("/dashboard/standards/nexre", headers={"X-MCP-User": "v"})
    assert r.status_code == 200
    body = r.text
    assert "AGENTS.md" in body
    assert "Frontmatter" in body
    assert 'data-tab="formatted"' in body
    assert 'data-tab="source"' in body
    assert 'data-tab="code"' in body
    # X-MCP-User header auth always resolves role=user: no Edit tab, no New doc button.
    assert 'data-tab="edit"' not in body
    assert "New doc" not in body


async def test_detail_page_base64_roundtrips_full_content(app_noauth) -> None:
    app, _ = app_noauth
    r = _client(app).get("/dashboard/standards/nexre", headers={"X-MCP-User": "v"})
    body = r.text
    marker = 'data-path="AGENTS.md"'
    idx = body.index(marker)
    card_html = body[max(0, idx - 400) : idx + 800]
    start = card_html.index('data-raw-b64="') + len('data-raw-b64="')
    end = card_html.index('"', start)
    raw_b64 = card_html[start:end]
    decoded = base64.b64decode(raw_b64).decode("utf-8")
    assert "title: Agents" in decoded
    assert "Body content that is definitely long enough" in decoded


# -- Admin-only guards (auth off => X-MCP-User is always role=user) --------


async def test_standard_new_view_requires_admin(app_noauth) -> None:
    app, _ = app_noauth
    r = _client(app).get("/dashboard/standards/nexre/new", headers={"X-MCP-User": "v"})
    assert r.status_code == 403


async def test_standard_create_requires_admin(app_noauth) -> None:
    app, _ = app_noauth
    r = _client(app).post(
        "/dashboard/standards/nexre/workflows/hotfix.md",
        data={"_csrf": "whatever", "content": "---\ntitle: X\n---\nbody"},
        headers={"X-MCP-User": "v"},
    )
    assert r.status_code == 403


async def test_delete_requires_admin(app_noauth) -> None:
    app, _ = app_noauth
    r = _client(app).post(
        "/dashboard/standards/nexre/AGENTS.md/delete",
        data={"_csrf": "whatever"},
        headers={"X-MCP-User": "v"},
    )
    assert r.status_code == 403


async def test_file_api_requires_admin(app_noauth) -> None:
    app, _ = app_noauth
    r = _client(app).post(
        "/dashboard/api/standards/nexre/file",
        json={"path": "AGENTS.md", "content": "x", "expected_version": 1},
        headers={"X-MCP-User": "v"},
    )
    assert r.status_code == 403


# -- Admin write paths (real login) -----------------------------------------


async def test_standard_create_and_delete_as_admin(app_admin) -> None:
    app, standards = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = client.get("/dashboard/standards/nexre/new")
    assert r.status_code == 200

    r = client.post(
        "/dashboard/standards/nexre/workflows/hotfix.md",
        data={
            "_csrf": csrf,
            "content": "---\ntitle: Hotfix\ndescription: Emergency fix flow.\n---\n\nStep one.\n",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard/standards/nexre"

    row = await standards.get_file("nexre", "workflows/hotfix.md")
    assert row is not None
    assert row.title == "Hotfix"
    assert row.description == "Emergency fix flow."

    r = client.post(
        "/dashboard/standards/nexre/workflows/hotfix.md/delete",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert await standards.get_file("nexre", "workflows/hotfix.md") is None


async def test_standard_create_duplicate_path_conflicts(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = client.post(
        "/dashboard/standards/nexre/AGENTS.md",
        data={"_csrf": csrf, "content": "---\ntitle: X\ndescription: Y\n---\n" + "Z" * 100},
    )
    assert r.status_code == 409


async def test_file_api_rejects_missing_csrf(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    _login(client, "admin", "adminpw")

    r = client.post(
        "/dashboard/api/standards/nexre/file",
        json={"path": "AGENTS.md", "content": "x", "expected_version": 1},
    )
    assert r.status_code == 403


async def test_file_api_rejects_path_traversal(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = client.post(
        "/dashboard/api/standards/nexre/file",
        json={"path": "../../etc/passwd", "content": "x", "expected_version": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


async def test_file_api_updates_and_returns_status(app_admin) -> None:
    app, standards = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    existing = await standards.get_file("nexre", "AGENTS.md")
    new_content = (
        "---\ntitle: Agents\ndescription: Agent guide.\n---\n\n"
        "Updated body content that is long enough to pass the content-length rule.\n"
        "```\ncode\n```\n"
    )
    r = client.post(
        "/dashboard/api/standards/nexre/file",
        json={
            "path": "AGENTS.md",
            "content": new_content,
            "expected_version": existing.version,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["version"] == existing.version + 1
    assert data["indicator"] == "green"
    assert any(rr["rule_id"] == "structured-content" and rr["passed"] for rr in data["rules"])

    updated = await standards.get_file("nexre", "AGENTS.md")
    assert updated.version == existing.version + 1
    assert "Updated body content" in updated.body


async def test_file_api_version_conflict_returns_409(app_admin) -> None:
    app, standards = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    existing = await standards.get_file("nexre", "AGENTS.md")
    stale_version = existing.version

    # Someone else updates first.
    await standards.upsert_file(
        project="nexre",
        relative_path="AGENTS.md",
        kind="markdown",
        title="Agents",
        description="Agent guide.",
        frontmatter="title: Agents\ndescription: Agent guide.",
        body="Someone else already changed this body to something else entirely here.",
        expected_version=stale_version,
    )

    r = client.post(
        "/dashboard/api/standards/nexre/file",
        json={
            "path": "AGENTS.md",
            "content": "---\ntitle: Agents\ndescription: Agent guide.\n---\nmine",
            "expected_version": stale_version,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409
    assert r.json()["error"] == "version_conflict"
