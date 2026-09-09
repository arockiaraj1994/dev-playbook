"""Tests for playbook_scaffold_standards and playbook_list_templates.

Includes the parity check that matters most: the tool and the dashboard wizard
call the same service, so the same inputs must produce byte-identical stores.
"""

from __future__ import annotations

import filecmp
from dataclasses import dataclass
from pathlib import Path

import pytest

import scaffold_service
from identity import Principal, principal_var
from standards_store import StandardsStore
from tools import scaffold, templates
from tools.common import POLICY

JAVA = {"package": "com.acme.billing"}


@dataclass
class Ctx:
    status: str = "ok"
    query: str | None = None
    doc_path: str | None = None
    top_result_path: str | None = None
    top_result_score: float | None = None


@pytest.fixture
async def store(tmp_path: Path) -> StandardsStore:
    s = StandardsStore(tmp_path / "standards.db")
    await s.init()
    return s


@pytest.fixture(autouse=True)
def open_policy():
    """Default to the local single-operator config; tests opt into auth."""
    POLICY.scaffold_enabled = True
    POLICY.auth_enabled = False
    yield
    POLICY.scaffold_enabled = True
    POLICY.auth_enabled = False


async def call(store: StandardsStore, **args) -> tuple[str, Ctx]:
    ctx = Ctx()
    result = await scaffold.dispatch(scaffold.NAME, args, ctx, store)
    assert result is not None
    return result[0].text, ctx


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_dry_run_writes_nothing(store: StandardsStore):
    body, ctx = await call(
        store, project="billing", languages=["java"], placeholders=JAVA, dry_run=True
    )
    assert ctx.status == "ok"
    assert "Nothing was written" in body
    assert "core/guardrails.md" in body
    assert await store.list_projects() == []


async def test_scaffold_creates_the_project(store: StandardsStore):
    body, ctx = await call(store, project="billing", languages=["java"], placeholders=JAVA)
    assert ctx.status == "ok"
    assert "Created standards project 'billing'" in body
    assert await store.list_projects() == ["billing"]
    files = await store.list_files("billing")
    assert any(f.relative_path == "core/guardrails.md" for f in files)
    assert any(f.relative_path.startswith("languages/java/") for f in files)


async def test_scaffold_records_the_caller_as_actor(store: StandardsStore):
    token = principal_var.set(Principal(user_id="u1", user_name="alice", role="admin"))
    try:
        await call(store, project="billing", languages=["java"], placeholders=JAVA)
    finally:
        principal_var.reset(token)
    row = await store.get_file("billing", "core/guardrails.md")
    assert row is not None and row.updated_by == "alice"


async def test_two_languages_share_one_guardrails_document(store: StandardsStore):
    """Language packs contribute into base-owned docs rather than each writing one."""
    await call(
        store,
        project="poly",
        languages=["java", "typescript"],
        placeholders=JAVA,
    )
    paths = [f.relative_path for f in await store.list_files("poly")]
    assert paths.count("core/guardrails.md") == 1
    assert any(p.startswith("languages/java/") for p in paths)
    assert any(p.startswith("languages/typescript/") for p in paths)


async def test_placeholder_value_reaches_the_documents(store: StandardsStore):
    await call(store, project="billing", languages=["java"], placeholders=JAVA)
    bodies = " ".join(f.body for f in await store.list_files("billing"))
    assert "com.acme.billing" in bodies
    assert "{{package}}" not in bodies


# ---------------------------------------------------------------------------
# Steering text - every error has to name a next move
# ---------------------------------------------------------------------------


async def test_missing_placeholder_names_it_and_says_where_to_look(store: StandardsStore):
    body, ctx = await call(store, project="billing", languages=["java"], dry_run=True)
    assert ctx.status == "error"
    assert "package" in body
    assert "playbook_list_templates" in body


async def test_unknown_language_lists_the_valid_ids(store: StandardsStore):
    body, ctx = await call(store, project="billing", languages=["cobol"], dry_run=True)
    assert ctx.status == "error"
    assert "cobol" in body
    assert "java" in body
    assert "playbook_list_templates" in body


async def test_no_language_points_at_the_catalog(store: StandardsStore):
    body, ctx = await call(store, project="billing", languages=[], dry_run=True)
    assert ctx.status == "error"
    assert "playbook_list_templates" in body


async def test_invalid_project_name_explains_the_shape(store: StandardsStore):
    body, ctx = await call(store, project="../etc", languages=["java"], placeholders=JAVA)
    assert ctx.status == "error"
    assert "not a usable project name" in body
    assert await store.list_projects() == []


async def test_unknown_rule_ids_are_rejected_with_the_namespace_hint(store: StandardsStore):
    body, ctx = await call(
        store,
        project="billing",
        languages=["java"],
        placeholders=JAVA,
        rule_ids=["java:no-such-rule"],
        dry_run=True,
    )
    assert ctx.status == "error"
    assert "java:no-such-rule" in body
    assert "namespaced" in body


async def test_existing_project_is_refused_not_merged(store: StandardsStore):
    await call(store, project="billing", languages=["java"], placeholders=JAVA)
    before = {f.relative_path: f.version for f in await store.list_files("billing")}

    body, ctx = await call(store, project="billing", languages=["java"], placeholders=JAVA)
    assert ctx.status == "error"
    assert "already exists" in body
    assert "never merges" in body
    assert "playbook_find_standards" in body

    after = {f.relative_path: f.version for f in await store.list_files("billing")}
    assert after == before


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def test_auth_off_allows_the_local_operator(store: StandardsStore):
    """With auth off every principal is role=user, so an admin gate would lock
    the tool out of the default local config entirely."""
    POLICY.auth_enabled = False
    _body, ctx = await call(store, project="billing", languages=["java"], placeholders=JAVA)
    assert ctx.status == "ok"


async def test_auth_on_refuses_a_non_admin(store: StandardsStore):
    POLICY.auth_enabled = True
    token = principal_var.set(Principal(user_id="u1", user_name="bob", role="user"))
    try:
        body, ctx = await call(store, project="billing", languages=["java"], placeholders=JAVA)
    finally:
        principal_var.reset(token)
    assert ctx.status == "error"
    assert "admin token" in body
    assert await store.list_projects() == []


async def test_auth_on_allows_an_admin(store: StandardsStore):
    POLICY.auth_enabled = True
    token = principal_var.set(Principal(user_id="u1", user_name="root", role="admin"))
    try:
        _body, ctx = await call(store, project="billing", languages=["java"], placeholders=JAVA)
    finally:
        principal_var.reset(token)
    assert ctx.status == "ok"


async def test_disabled_scaffolding_refuses_and_says_reads_still_work(store: StandardsStore):
    POLICY.scaffold_enabled = False
    body, ctx = await call(store, project="billing", languages=["java"], placeholders=JAVA)
    assert ctx.status == "error"
    assert "disabled on this server" in body
    assert "playbook_get_standard" in body


# ---------------------------------------------------------------------------
# Argument coercion - a model that sends a string where a list was asked for
# should get standards, not a stack trace.
# ---------------------------------------------------------------------------


async def test_languages_accepts_a_bare_string(store: StandardsStore):
    _body, ctx = await call(
        store, project="billing", languages="java", placeholders=JAVA, dry_run=True
    )
    assert ctx.status == "ok"


async def test_dry_run_accepts_the_string_true(store: StandardsStore):
    await call(store, project="billing", languages=["java"], placeholders=JAVA, dry_run="true")
    assert await store.list_projects() == []


# ---------------------------------------------------------------------------
# Wizard parity - the whole reason scaffold_service exists
# ---------------------------------------------------------------------------


async def test_tool_and_service_produce_identical_stores(tmp_path: Path):
    """The dashboard wizard calls scaffold_service directly. If the tool's own
    store ever differs from the service's, the shared-service claim is false."""
    via_tool = StandardsStore(tmp_path / "tool.db")
    await via_tool.init()
    await call(via_tool, project="billing", languages=["java"], placeholders=JAVA)

    via_wizard = StandardsStore(tmp_path / "wizard.db")
    await via_wizard.init()
    await scaffold_service.scaffold_project(
        via_wizard, languages=["java"], project="billing", placeholders=JAVA
    )

    left, right = tmp_path / "left", tmp_path / "right"
    await via_tool.dump_to_dir(left)
    await via_wizard.dump_to_dir(right)

    match, mismatch, errors = filecmp.cmpfiles(
        left / "billing",
        right / "billing",
        [
            str(p.relative_to(left / "billing"))
            for p in (left / "billing").rglob("*")
            if p.is_file()
        ],
        shallow=False,
    )
    assert not mismatch and not errors
    assert match


# ---------------------------------------------------------------------------
# playbook_list_templates
# ---------------------------------------------------------------------------


async def test_catalog_lists_packs_and_their_placeholders(store: StandardsStore):
    ctx = Ctx()
    result = await templates.dispatch(templates.NAME, {}, ctx, store)
    assert result is not None
    body = result[0].text
    assert "`java`" in body
    assert "package" in body
    assert "playbook_scaffold_standards" in body


async def test_catalog_detail_names_rules_and_locked_flags(store: StandardsStore):
    ctx = Ctx()
    result = await templates.dispatch(templates.NAME, {"language": "java"}, ctx, store)
    assert result is not None
    body = result[0].text
    assert "Required placeholders" in body
    assert "java:" in body


async def test_catalog_detail_for_unknown_pack_steers(store: StandardsStore):
    ctx = Ctx()
    result = await templates.dispatch(templates.NAME, {"language": "cobol"}, ctx, store)
    assert result is not None
    assert ctx.status == "error"
    assert "Available ids" in result[0].text
