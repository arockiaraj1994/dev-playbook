"""GET /api/projects/{name} - the plugin hooks' one question to the server (#635)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from tools.common import match_project_name, normalize_project_name


async def _build(tmp_path: Path, *, auth_enabled: bool):
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
    for rel, body in (
        ("guardrails.md", "# Guardrails\n\n- rule"),
        ("gates/definition-of-done.md", "# DoD\n\n- done"),
    ):
        await standards.upsert_file(
            project="my-trip",
            relative_path=rel,
            kind="markdown",
            title=rel,
            description="",
            frontmatter="",
            body=body,
            expected_version=None,
        )

    cfg = (
        server.McpConfig(auth_enabled=True, admin_username="admin", admin_password="adminpw")
        if auth_enabled
        else server.McpConfig(auth_enabled=False)
    )
    app = server.build_app(
        server.AppDeps(
            cfg=cfg, metrics=metrics, inactive_days=2, auth_store=auth_store, standards=standards
        ),
    )
    return app, auth_store


@pytest.fixture
async def client(tmp_path: Path):
    app, _ = await _build(tmp_path, auth_enabled=False)
    return TestClient(app)


async def test_lookup_returns_the_guardrails_and_definition_of_done(client) -> None:
    resp = client.get("/api/projects/my-trip")
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["project"] == "my-trip"
    assert "- rule" in body["guardrails"]
    assert "- done" in body["definition_of_done"]


@pytest.mark.parametrize("name", ["mytrip", "My-Trip", "my_trip", "MY.TRIP"])
async def test_lookup_ignores_case_and_separators(client, name: str) -> None:
    body = client.get(f"/api/projects/{name}").json()
    assert body["exists"] is True
    assert body["project"] == "my-trip"


async def test_lookup_of_an_unknown_project_says_so(client) -> None:
    resp = client.get("/api/projects/projects")
    assert resp.status_code == 200
    assert resp.json() == {"exists": False, "project": None}


async def test_lookup_rejects_a_malformed_name(client) -> None:
    resp = client.get("/api/projects/" + "x" * 65)
    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid_project_name"}


async def test_lookup_needs_a_bearer_token_when_auth_is_on(tmp_path: Path) -> None:
    app, auth_store = await _build(tmp_path, auth_enabled=True)
    client = TestClient(app)
    assert client.get("/api/projects/my-trip").status_code == 401

    user = await auth_store.verify_login("admin", "adminpw")
    token = (await auth_store.create_token(user["id"], None, token_type="mcp"))["token"]
    resp = client.get("/api/projects/my-trip", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["exists"] is True


def test_normalize_drops_case_and_separators() -> None:
    assert normalize_project_name("My_Trip.app-x") == "mytripappx"


def test_match_prefers_exact_then_case_then_folded() -> None:
    names = ["my-trip", "My-Trip-Old", "nexre"]
    assert match_project_name(names, "my-trip") == "my-trip"
    assert match_project_name(names, "NEXRE") == "nexre"
    assert match_project_name(names, "mytrip") == "my-trip"
    assert match_project_name(names, "") is None


def test_an_ambiguous_folded_match_is_refused() -> None:
    assert match_project_name(["my-trip", "my_trip"], "mytrip") is None
