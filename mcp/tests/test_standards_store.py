"""Tests for standards_store.py (CRUD, version conflicts, seed, dump)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from standards_store import StandardsStore, VersionConflict


@pytest.fixture
async def store(tmp_path: Path) -> StandardsStore:
    s = StandardsStore(tmp_path / "standards.db")
    await s.init()
    return s


async def test_list_projects_empty(store: StandardsStore):
    assert await store.list_projects() == []


async def test_upsert_creates_project_and_file(store: StandardsStore):
    row = await store.upsert_file(
        project="nexre",
        relative_path="AGENTS.md",
        kind="markdown",
        title="Agents",
        description="Agent instructions",
        frontmatter="title: Agents\ndescription: Agent instructions",
        body="Body text here.",
        expected_version=None,
        updated_by="alice",
    )
    assert row.version == 1
    assert row.title == "Agents"
    assert await store.list_projects() == ["nexre"]
    fetched = await store.get_file("nexre", "AGENTS.md")
    assert fetched is not None
    assert fetched.body == "Body text here."
    assert fetched.updated_by == "alice"


async def test_upsert_existing_path_without_expected_version_conflicts(store: StandardsStore):
    await store.upsert_file(
        project="p",
        relative_path="a.md",
        kind="markdown",
        title="A",
        body="x" * 100,
        expected_version=None,
    )
    with pytest.raises(VersionConflict):
        await store.upsert_file(
            project="p",
            relative_path="a.md",
            kind="markdown",
            title="A2",
            body="y" * 100,
            expected_version=None,
        )


async def test_update_with_correct_version_succeeds(store: StandardsStore):
    row = await store.upsert_file(
        project="p",
        relative_path="a.md",
        kind="markdown",
        title="A",
        body="x" * 100,
        expected_version=None,
    )
    updated = await store.upsert_file(
        project="p",
        relative_path="a.md",
        kind="markdown",
        title="A revised",
        body="z" * 100,
        expected_version=row.version,
    )
    assert updated.version == 2
    assert updated.title == "A revised"


async def test_update_with_stale_version_raises_conflict(store: StandardsStore):
    row = await store.upsert_file(
        project="p",
        relative_path="a.md",
        kind="markdown",
        title="A",
        body="x" * 100,
        expected_version=None,
    )
    # Someone else updates first.
    await store.upsert_file(
        project="p",
        relative_path="a.md",
        kind="markdown",
        title="A2",
        body="y" * 100,
        expected_version=row.version,
    )
    # Now our stale version fails.
    with pytest.raises(VersionConflict) as exc_info:
        await store.upsert_file(
            project="p",
            relative_path="a.md",
            kind="markdown",
            title="A3",
            body="w" * 100,
            expected_version=row.version,
        )
    assert exc_info.value.expected == row.version
    assert exc_info.value.actual == row.version + 1


async def test_delete_file_removes_row(store: StandardsStore):
    await store.upsert_file(
        project="p",
        relative_path="a.md",
        kind="markdown",
        title="A",
        body="x" * 100,
        expected_version=None,
    )
    deleted = await store.delete_file("p", "a.md")
    assert deleted is True
    assert await store.get_file("p", "a.md") is None


async def test_delete_last_file_removes_project(store: StandardsStore):
    await store.upsert_file(
        project="p",
        relative_path="a.md",
        kind="markdown",
        title="A",
        body="x" * 100,
        expected_version=None,
    )
    await store.delete_file("p", "a.md")
    assert await store.list_projects() == []


async def test_delete_missing_file_returns_false(store: StandardsStore):
    assert await store.delete_file("nope", "a.md") is False


async def _seed_two_projects(store: StandardsStore) -> None:
    for project, paths in (("keep", ["a.md", "b.md"]), ("doomed", ["a.md", "b.md", "c.md"])):
        for path in paths:
            await store.upsert_file(
                project=project,
                relative_path=path,
                kind="markdown",
                title="A",
                body="x" * 100,
                expected_version=None,
            )


async def test_delete_project_removes_every_document(store: StandardsStore):
    await _seed_two_projects(store)

    count = await store.delete_project("doomed")

    assert count == 3
    assert await store.get_project("doomed") is None
    assert await store.list_files("doomed") == []


async def test_delete_project_leaves_other_projects_alone(store: StandardsStore):
    """The blast radius is exactly one project."""
    await _seed_two_projects(store)

    await store.delete_project("doomed")

    assert await store.list_projects() == ["keep"]
    assert len(await store.list_files("keep")) == 2


async def test_delete_missing_project_returns_zero(store: StandardsStore):
    assert await store.delete_project("nope") == 0


async def test_list_files_ordered_by_path(store: StandardsStore):
    await store.upsert_file(
        project="p",
        relative_path="b.md",
        kind="markdown",
        title="B",
        body="x" * 100,
        expected_version=None,
    )
    await store.upsert_file(
        project="p",
        relative_path="a.md",
        kind="markdown",
        title="A",
        body="x" * 100,
        expected_version=None,
    )
    files = await store.list_files("p")
    assert [f.relative_path for f in files] == ["a.md", "b.md"]


def _seed_payload() -> list[dict]:
    return [
        {
            "project": "nexre",
            "relative_path": "AGENTS.md",
            "kind": "markdown",
            "is_executable": False,
            "content": "---\ntitle: Agents\ndescription: Agent guide.\n---\nBody content here that is long enough.",
        },
        {
            "project": "nexre",
            "relative_path": "gates/scripts/check.sh",
            "kind": "script",
            "is_executable": True,
            "content": "#!/bin/bash\necho ok\n",
        },
    ]


async def test_seed_from_json_loads_entries(store: StandardsStore, tmp_path: Path):
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_seed_payload()))

    n = await store.seed_from_json(seed_path)
    assert n == 2
    assert await store.list_projects() == ["nexre"]

    agents = await store.get_file("nexre", "AGENTS.md")
    assert agents is not None
    assert agents.title == "Agents"
    assert agents.description == "Agent guide."

    script = await store.get_file("nexre", "gates/scripts/check.sh")
    assert script is not None
    assert script.is_executable is True
    assert script.kind == "script"


async def test_seed_from_json_is_idempotent(store: StandardsStore, tmp_path: Path):
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_seed_payload()))

    first = await store.seed_from_json(seed_path)
    second = await store.seed_from_json(seed_path)
    assert first == 2
    assert second == 0  # already seeded, no-op without --force


async def test_seed_from_json_force_reloads(store: StandardsStore, tmp_path: Path):
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(_seed_payload()))

    await store.seed_from_json(seed_path)
    # Mutate a row, then force-reseed should reset it.
    row = await store.get_file("nexre", "AGENTS.md")
    await store.upsert_file(
        project="nexre",
        relative_path="AGENTS.md",
        kind="markdown",
        title="Mutated",
        body="mutated body",
        expected_version=row.version,
    )
    n = await store.seed_from_json(seed_path, force=True)
    assert n == 2
    restored = await store.get_file("nexre", "AGENTS.md")
    assert restored.title == "Agents"


async def test_seed_from_json_missing_file_is_noop(store: StandardsStore, tmp_path: Path):
    n = await store.seed_from_json(tmp_path / "does-not-exist.json")
    assert n == 0
    assert await store.list_projects() == []


async def test_dump_to_dir_writes_files(store: StandardsStore, tmp_path: Path):
    await store.upsert_file(
        project="p",
        relative_path="core/a.md",
        kind="markdown",
        title="A",
        description="d",
        frontmatter="title: A\ndescription: d",
        body="Body text.",
        expected_version=None,
    )
    await store.upsert_file(
        project="p",
        relative_path="gates/scripts/check.sh",
        kind="script",
        title="check.sh",
        body="#!/bin/bash",
        is_executable=True,
        expected_version=None,
    )

    target = tmp_path / "dump"
    n = await store.dump_to_dir(target)
    assert n == 2

    md_content = (target / "p" / "core" / "a.md").read_text()
    assert "title: A" in md_content
    assert "Body text." in md_content
    assert (target / "p" / "gates" / "scripts" / "check.sh").read_text() == "#!/bin/bash"


# ---------------------------------------------------------------------------
# Migration: pre-v2 core/ layout -> v2 paths (#635)
# ---------------------------------------------------------------------------


async def _seed_core_layout(store: StandardsStore, project: str) -> None:
    for rel, body in (
        ("core/guardrails.md", "Rules. See `core/definition-of-done.md` before done."),
        ("core/definition-of-done.md", "Checklist."),
        ("core/git.md", "Git."),
        ("core/glossary.md", "Words."),
        ("workflows/bug-fix.md", "Re-read `core/guardrails.md` first."),
    ):
        await store.upsert_file(
            project=project,
            relative_path=rel,
            kind="markdown",
            title=rel,
            description="",
            frontmatter="see_also: [core/git.md]" if rel == "workflows/bug-fix.md" else "",
            body=body,
            expected_version=None,
        )


async def test_init_moves_core_documents_to_their_v2_paths(tmp_path: Path):
    store = StandardsStore(tmp_path / "standards.db")
    await store.init()
    await _seed_core_layout(store, "old")

    await StandardsStore(tmp_path / "standards.db").init()

    paths = {f.relative_path for f in await store.list_files("old")}
    assert paths == {
        "guardrails.md",
        "gates/definition-of-done.md",
        "git.md",
        "glossary.md",
        "workflows/bug-fix.md",
    }
    guardrails = await store.get_file("old", "guardrails.md")
    assert guardrails is not None
    assert "`gates/definition-of-done.md`" in guardrails.body
    bug_fix = await store.get_file("old", "workflows/bug-fix.md")
    assert bug_fix is not None
    assert "core/" not in bug_fix.body
    assert bug_fix.frontmatter == "see_also: [git.md]"


async def test_the_core_migration_never_overwrites_an_existing_v2_document(tmp_path: Path):
    store = StandardsStore(tmp_path / "standards.db")
    await store.init()
    await _seed_core_layout(store, "both")
    await store.upsert_file(
        project="both",
        relative_path="guardrails.md",
        kind="markdown",
        title="v2",
        description="",
        frontmatter="",
        body="The v2 guardrails.",
        expected_version=None,
    )

    await StandardsStore(tmp_path / "standards.db").init()

    kept = await store.get_file("both", "guardrails.md")
    assert kept is not None and kept.body == "The v2 guardrails."
    assert await store.get_file("both", "core/guardrails.md") is not None


async def test_the_core_migration_is_idempotent(tmp_path: Path):
    store = StandardsStore(tmp_path / "standards.db")
    await store.init()
    await _seed_core_layout(store, "twice")
    await StandardsStore(tmp_path / "standards.db").init()
    first = {f.relative_path: f.body for f in await store.list_files("twice")}
    await StandardsStore(tmp_path / "standards.db").init()
    second = {f.relative_path: f.body for f in await store.list_files("twice")}
    assert first == second
