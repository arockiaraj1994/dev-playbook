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
    assert 'data-tab="code"' not in body
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


# -- Delete a whole project -------------------------------------------------


async def test_delete_project_requires_admin(app_noauth) -> None:
    app, standards = app_noauth
    r = _client(app).post(
        "/dashboard/standards/nexre/delete-project",
        data={"_csrf": "whatever", "confirm": "nexre"},
        headers={"X-MCP-User": "v"},
    )
    assert r.status_code == 403
    assert await standards.get_project("nexre") is not None


async def test_delete_project_rejects_missing_csrf(app_admin) -> None:
    app, standards = app_admin
    client = _client(app)
    _login(client, "admin", "adminpw")

    r = client.post("/dashboard/standards/nexre/delete-project", data={"confirm": "nexre"})
    assert r.status_code == 403
    assert await standards.get_project("nexre") is not None


async def test_delete_project_requires_the_typed_name(app_admin) -> None:
    """The confirmation is a real check: a mis-aimed POST must not destroy a corpus."""
    app, standards = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    # Case-sensitive and non-empty. Surrounding whitespace is tolerated, since a
    # project name cannot contain spaces and a stray one from copy-paste should
    # not block a deliberate deletion.
    for confirm in ("", "wrong", "NEXRE", "nexre2"):
        r = client.post(
            "/dashboard/standards/nexre/delete-project",
            data={"_csrf": csrf, "confirm": confirm},
        )
        assert r.status_code == 400, f"{confirm!r} should not have confirmed"
        assert await standards.get_project("nexre") is not None


async def test_delete_project_removes_it(app_admin) -> None:
    app, standards = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = client.post(
        "/dashboard/standards/nexre/delete-project",
        data={"_csrf": csrf, "confirm": "nexre"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/dashboard/standards?deleted=1"

    assert await standards.get_project("nexre") is None
    assert await standards.list_projects() == []
    assert await standards.get_file("nexre", "AGENTS.md") is None


async def test_delete_unknown_project_is_404(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = client.post(
        "/dashboard/standards/ghost/delete-project",
        data={"_csrf": csrf, "confirm": "ghost"},
    )
    assert r.status_code == 404


async def test_delete_project_only_touches_that_project(app_admin) -> None:
    app, standards = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    await standards.upsert_file(
        project="other", relative_path="AGENTS.md", kind="markdown",
        title="Other", description="d", frontmatter="", body="y" * 100,
        expected_version=None,
    )

    client.post(
        "/dashboard/standards/nexre/delete-project",
        data={"_csrf": csrf, "confirm": "nexre"},
        follow_redirects=False,
    )

    assert await standards.list_projects() == ["other"]
    assert await standards.get_file("other", "AGENTS.md") is not None


async def test_detail_page_shows_delete_zone_to_admin(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    _login(client, "admin", "adminpw")

    body = client.get("/dashboard/standards/nexre").text
    assert "delete-project" in body
    assert 'name="confirm"' in body


async def test_detail_page_hides_delete_zone_from_viewer(app_noauth) -> None:
    app, _ = app_noauth
    body = _client(app).get("/dashboard/standards/nexre", headers={"X-MCP-User": "v"}).text
    assert "delete-project" not in body


# -- New project wizard -----------------------------------------------------


def _walk_to_step(client, csrf, step: str, **overrides):
    """Drive the wizard as a browser would, posting the whole state each time."""
    data = {
        "_csrf": csrf,
        "project": overrides.get("project", "payments"),
        "language": overrides.get("languages", ["java", "typescript"]),
        "placeholder_package": overrides.get("package", "com.example.payments"),
    }
    data.update(overrides.get("extra", {}))
    return client.post(f"/dashboard/standards/new-project/{step}", data=data)


# The rule steps are now one per pack: base, then each selected language.
RULES_BASE = "rules/base"
RULES_JAVA = "rules/java"


async def test_wizard_step1_lists_every_language(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    _login(client, "admin", "adminpw")

    body = client.get("/dashboard/standards/new-project").text
    assert "New Project Standards" in body
    for lang in ("java", "typescript", "kotlin"):
        assert f'value="{lang}"' in body
    # Languages are checkboxes now, not a single-choice radio.
    assert 'type="checkbox" name="language"' in body
    assert "stepper" in body


async def test_wizard_requires_admin(app_noauth) -> None:
    app, _ = app_noauth
    r = _client(app).get("/dashboard/standards/new-project", headers={"X-MCP-User": "v"})
    assert r.status_code == 403


async def test_wizard_rejects_bad_project_name(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = _walk_to_step(client, csrf, RULES_BASE, project="../etc")
    assert r.status_code == 400
    assert "Project name must be" in r.text


async def test_wizard_requires_a_language(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = client.post(
        "/dashboard/standards/new-project/rules/base",
        data={"_csrf": csrf, "project": "payments"},
    )
    assert r.status_code == 400
    assert "at least one language" in r.text


async def test_wizard_requires_placeholders_the_languages_declare(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = client.post(
        "/dashboard/standards/new-project/rules/base",
        data={"_csrf": csrf, "project": "payments", "language": ["java"]},
    )
    assert r.status_code == 400
    assert "package" in r.text


async def test_step_count_follows_the_language_selection(app_admin) -> None:
    """Six steps for one language, eight for three - the whole point of splitting."""
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    one = _walk_to_step(client, csrf, RULES_BASE, languages=["java"]).text
    assert one.count("stepper-item") == 6

    three = _walk_to_step(
        client, csrf, RULES_BASE, languages=["java", "kotlin", "typescript"]
    ).text
    assert three.count("stepper-item") == 8
    for label in ("Java rules", "Kotlin rules", "TypeScript rules"):
        assert label in three


async def test_shared_step_shows_only_shared_rules(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    body = _walk_to_step(client, csrf, RULES_BASE).text
    assert "base:no-hardcoded-secrets" in body
    assert "java:no-raw-types" not in body
    assert "typescript:no-any" not in body
    # Git rules live on their own step, not among the shared ones.
    assert "base:conventional-commits" not in body


async def test_language_step_shows_only_that_language(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    body = _walk_to_step(client, csrf, RULES_JAVA).text
    assert "java:no-raw-types" in body
    assert "typescript:no-any" not in body
    assert "kotlin:no-globalscope" not in body


async def test_rule_categories_are_collapsed_with_friendly_names(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    body = _walk_to_step(client, csrf, RULES_JAVA).text
    assert "recommended rules enabled" in body
    assert "rule-category" in body
    # Category names come from the pack's `label:`, not the filename.
    for label in ("Everyday rules", "Architecture &amp; boundaries", "Common mistakes"):
        assert label in body
    assert "anti-patterns" not in body


async def test_unknown_rule_step_is_404(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = _walk_to_step(client, csrf, "rules/cobol")
    assert r.status_code == 404


async def test_titles_are_short_and_readable() -> None:
    """A picker of long or one-word labels is exactly what this change fixed."""
    from templates_store import load_packs

    for pack in load_packs().values():
        for rule in pack.all_rules:
            assert len(rule.title) <= 60, f"{rule.id}: title too long for a checkbox"
            assert len(rule.title.split()) >= 2, f"{rule.id}: one-word title says nothing"


async def test_wizard_step3_shows_git_rules(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    body = _walk_to_step(client, csrf, "git-rules").text
    assert "base:conventional-commits" in body
    assert "base:no-secrets-in-history" in body
    assert "core/git.md" in body


async def test_wizard_step4_locks_required_workflows(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    body = _walk_to_step(client, csrf, "workflows").text
    assert "new-feature" in body and "release" in body
    assert "required" in body


async def test_wizard_step5_previews_every_document(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    body = _walk_to_step(client, csrf, "review").text
    assert "core/guardrails.md" in body
    assert "core/git.md" in body
    assert "languages/java/standards.md" in body
    assert "languages/typescript/standards.md" in body
    # Edit buttons let the user go back to any step from the review.
    assert 'formaction="/dashboard/standards/new-project/workflows"' in body


async def test_wizard_back_preserves_state(app_admin) -> None:
    """Back from step 2 re-renders step 1 with the selection intact."""
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = client.post(
        "/dashboard/standards/new-project",
        data={
            "_csrf": csrf,
            "project": "payments",
            "language": ["java", "typescript"],
            "placeholder_package": "com.example.payments",
        },
    )
    assert r.status_code == 200
    assert 'value="payments"' in r.text
    assert 'value="com.example.payments"' in r.text


async def test_wizard_creates_multi_language_project(app_admin) -> None:
    from standards_scanner import scan_project

    app, standards = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = _walk_to_step(client, csrf, "create")
    assert r.status_code in (303, 200)
    if r.status_code == 303:
        assert r.headers["location"] == "/dashboard/standards/payments"

    status = await scan_project(standards, "payments")
    assert status.missing_required == []
    assert status.indicator == "green"

    project = await standards.get_project("payments")
    assert {p["id"] for p in project.packs} == {"base", "java", "typescript"}


async def test_wizard_create_forces_locked_rules_and_workflows(app_admin) -> None:
    """A forged POST that drops every rule and workflow must not drop them."""
    from standards_scanner import REQUIRED_WORKFLOWS
    from templates_store import base_pack, load_packs

    app, standards = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    r = client.post(
        "/dashboard/standards/new-project/create",
        data={
            "_csrf": csrf,
            "project": "forged",
            "language": ["java"],
            "placeholder_package": "com.example.forged",
            # no language_rule, no git_rule, no workflow
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    for wid in REQUIRED_WORKFLOWS:
        assert await standards.get_file("forged", f"workflows/{wid}.md") is not None

    # Map each locked rule to the document that carries it, then look there.
    owner: dict[str, str] = {}
    for pack in (base_pack(), load_packs()["java"]):
        for doc in pack.rule_docs:
            for rule in doc.rules:
                owner[rule.id] = doc.doc
        for contribution in pack.contributions:
            for rule in contribution.rules:
                owner[rule.id] = contribution.contributes_to

    for pack in (base_pack(), load_packs()["java"]):
        for rule in pack.all_rules:
            if not rule.locked:
                continue
            row = await standards.get_file("forged", owner[rule.id])
            assert row is not None, f"{owner[rule.id]} was not created"
            assert rule.local_id in row.body, (
                f"locked rule {rule.id} was dropped from {owner[rule.id]}"
            )


async def test_collapsed_categories_still_submit_their_rules(app_admin) -> None:
    """The whole "accept the recommended set in one click" design rests on this.

    A closed <details> keeps its checkboxes in the DOM, so they still submit.
    Walk every rule step taking whatever it pre-ticked, without expanding
    anything, and assert the defaults reach the created project.
    """
    import re

    from templates_store import base_pack, load_packs

    app, standards = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    state: dict = {
        "_csrf": csrf,
        "project": "collapsed",
        "language": ["java"],
        "placeholder_package": "com.example.collapsed",
    }
    for step in ("rules/base", "rules/java", "git-rules", "workflows"):
        body = client.post(f"/dashboard/standards/new-project/{step}", data=state).text
        # Take exactly what the rendered form would submit, untouched - no
        # expanding, no ticking. This is the "accept the defaults" path.
        for field in ("language_rule", "git_rule", "workflow"):
            found = re.findall(rf'name="{field}" value="([^"]+)"', body)
            if found:
                state[field] = list(dict.fromkeys(found))

    r = client.post(
        "/dashboard/standards/new-project/create", data=state, follow_redirects=False
    )
    assert r.status_code == 303

    # source_rule_ids records exactly which rules produced each document, so
    # compare that against every default-on rule of the packs involved.
    import json
    import sqlite3

    conn = sqlite3.connect(str(standards.path))
    stored: set[str] = set()
    for (ids,) in conn.execute(
        "SELECT source_rule_ids FROM standards_files WHERE project = 'collapsed'"
    ):
        stored |= set(json.loads(ids or "[]"))
    conn.close()

    expected = base_pack().default_rule_ids() | load_packs()["java"].default_rule_ids()
    assert expected <= stored, f"collapsed walk dropped {sorted(expected - stored)}"

    from standards_scanner import scan_project

    assert (await scan_project(standards, "collapsed")).indicator == "green"


async def test_wizard_create_rejects_missing_csrf(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    _login(client, "admin", "adminpw")

    r = client.post(
        "/dashboard/standards/new-project/create",
        data={"project": "nocsrf", "language": ["typescript"]},
    )
    assert r.status_code == 403


async def test_wizard_duplicate_project_conflicts(app_admin) -> None:
    app, _ = app_admin
    client = _client(app)
    csrf = _login(client, "admin", "adminpw")

    first = _walk_to_step(client, csrf, "create")
    assert first.status_code in (303, 200)

    second = client.post(
        "/dashboard/standards/new-project/create",
        data={
            "_csrf": csrf,
            "project": "payments",
            "language": ["java", "typescript"],
            "placeholder_package": "com.example.payments",
        },
        follow_redirects=False,
    )
    assert second.status_code == 409

async def test_standard_new_view_offers_architecture_starter(app_admin) -> None:
    """The New-doc page ships a starter template for the required ARCHITECTURE.md."""
    app, _ = app_admin
    client = _client(app)
    _login(client, "admin", "adminpw")

    body = client.get("/dashboard/standards/nexre/new").text
    assert 'id="starter"' in body
    assert 'value="architecture"' in body
    # The starter must prefill the exact required path, or it won't satisfy the rule.
    assert "ARCHITECTURE.md" in body


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
