"""Tests for template packs: loading, validation, composition and scaffolding.

The load-bearing test is test_every_language_combination_scaffolds_green - it is
what guarantees no combination of languages can produce a broken project.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
import yaml

import scaffold_service as svc
import templates_store
from standards_scanner import REQUIRED_FILES, REQUIRED_WORKFLOWS, scan_project
from standards_store import ProjectExists, StandardsStore
from templates_store import TemplateError, base_pack, compose, load_packs, substitute

LANGUAGE_IDS = ["java", "kotlin", "typescript"]

# Every language pack that declares a placeholder needs a value here. A new
# placeholder makes these tests fail, which is the intended nudge.
PLACEHOLDERS = {"package": "com.example.demo"}

COMBINATIONS = [
    list(combo)
    for n in range(1, len(LANGUAGE_IDS) + 1)
    for combo in itertools.combinations(LANGUAGE_IDS, n)
]


@pytest.fixture
async def store(tmp_path: Path) -> StandardsStore:
    s = StandardsStore(tmp_path / "metrics.db")
    await s.init()
    return s


# -- loading and validation -------------------------------------------------


def test_base_pack_loads():
    base = base_pack()
    assert base is not None
    assert base.kind == "base"
    assert base.workflows, "the base pack must ship workflow documents"


def test_every_language_pack_loads():
    packs = load_packs()
    for lang in LANGUAGE_IDS:
        assert lang in packs, f"{lang} failed to load (a malformed pack is skipped silently)"
        assert packs[lang].kind == "language"
        assert packs[lang].gate, f"{lang} must declare a gate script"


def test_rule_ids_are_namespaced_by_pack():
    """Several packs legitimately define a rule with the same local name."""
    for pack in load_packs().values():
        for rule in pack.all_rules:
            assert rule.id == f"{pack.id}:{rule.local_id}"


def test_rule_ids_are_globally_unique_after_namespacing():
    seen: dict[str, str] = {}
    for pack in load_packs().values():
        for rule in pack.all_rules:
            assert rule.id not in seen, f"{rule.id} defined twice"
            seen[rule.id] = pack.id


def test_locked_rules_are_default_on():
    for pack in load_packs().values():
        for rule in pack.all_rules:
            if rule.locked:
                assert rule.default, f"{rule.id} is locked but not default-on"


def test_required_workflows_all_exist():
    base = base_pack()
    shipped = {w.id for w in base.workflows}
    assert set(REQUIRED_WORKFLOWS) <= shipped
    for wid in REQUIRED_WORKFLOWS:
        wf = next(w for w in base.workflows if w.id == wid)
        assert wf.required, f"{wid} is required by the scanner but not flagged required"


def test_malformed_pack_is_skipped_not_fatal(tmp_path: Path, monkeypatch):
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "pack.yaml").write_text("id: broken\nkind: language\n", encoding="utf-8")
    monkeypatch.setattr(templates_store, "find_manifests", lambda: [bad / "pack.yaml"])

    assert load_packs() == {}


def test_contribution_to_unknown_group_is_rejected(tmp_path: Path):
    root = tmp_path / "bad"
    (root / "rules").mkdir(parents=True)
    (root / "pack.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "bad",
                "kind": "language",
                "title": "Bad",
                "language": "bad",
                "language_version": "1",
                "template_version": "1.0.0",
                "gate": "gates/scripts/verify-bad.sh",
            }
        ),
        encoding="utf-8",
    )
    (root / "rules" / "g.yaml").write_text(
        yaml.safe_dump(
            {
                "contributes_to": "core/guardrails.md",
                "section": "Bad",
                "rules": [{"id": "x", "group": "no-such-group", "title": "X"}],
            }
        ),
        encoding="utf-8",
    )
    pack = templates_store._load_pack(root / "pack.yaml")

    with pytest.raises(TemplateError, match="not declared by"):
        compose(base_pack(), [pack])


# -- composition ------------------------------------------------------------


@pytest.mark.parametrize("languages", COMBINATIONS, ids=lambda ls: "+".join(ls))
def test_no_two_packs_produce_the_same_path(languages):
    """The defect this whole design exists to prevent."""
    docs = svc.preview(languages, "demo", PLACEHOLDERS)
    paths = [d.relative_path for d in docs]
    assert len(paths) == len(set(paths)), "a document path was produced twice"


@pytest.mark.parametrize("languages", COMBINATIONS, ids=lambda ls: "+".join(ls))
def test_composition_ships_every_required_document(languages):
    paths = {d.relative_path for d in svc.preview(languages, "demo", PLACEHOLDERS)}
    required = set(REQUIRED_FILES) | {f"workflows/{w}.md" for w in REQUIRED_WORKFLOWS}
    assert required <= paths, f"missing {sorted(required - paths)}"


@pytest.mark.parametrize("languages", COMBINATIONS, ids=lambda ls: "+".join(ls))
def test_no_unresolved_placeholders(languages):
    for doc in svc.preview(languages, "demo", PLACEHOLDERS):
        assert "{{" not in doc.content, f"{doc.relative_path} has a placeholder left"


@pytest.mark.parametrize("languages", COMBINATIONS, ids=lambda ls: "+".join(ls))
def test_rendered_markdown_is_well_formed(languages):
    """Frontmatter must parse and every doc needs a table or code block, or the
    scanner marks it amber."""
    from standards_scanner import _parse_frontmatter

    for doc in svc.preview(languages, "demo", PLACEHOLDERS):
        if not doc.relative_path.endswith(".md"):
            continue
        meta, body = _parse_frontmatter(doc.content)
        assert meta.get("title"), f"{doc.relative_path} has no title"
        assert meta.get("description"), f"{doc.relative_path} has no description"
        assert "|" in body or "```" in body, f"{doc.relative_path} has no table or code block"


def test_multi_language_guardrails_carry_a_section_per_language():
    docs = {d.relative_path: d for d in svc.preview(["java", "typescript"], "demo", PLACEHOLDERS)}
    content = docs["core/guardrails.md"].content

    assert "### Java" in content
    assert "### TypeScript" in content
    # Java-only and TypeScript-only rules land in their own sections.
    assert "no-raw-types" in content
    assert "no-any" in content
    # Shared rules come from base exactly once.
    assert content.count("**No hardcoded secrets.**") == 1


def test_single_language_renders_no_section_headings():
    """A one-language project must read as it did before packs existed."""
    docs = {d.relative_path: d for d in svc.preview(["java"], "demo", PLACEHOLDERS)}
    content = docs["core/guardrails.md"].content

    assert "### Java" not in content
    assert "no-raw-types" in content


def test_architecture_merges_by_language():
    docs = {d.relative_path: d for d in svc.preview(["java", "typescript"], "demo", PLACEHOLDERS)}
    content = docs["ARCHITECTURE.md"].content

    assert "## Java" in content
    assert "## TypeScript" in content
    # Each language brings its own module table.
    assert "com.example.demo.domain" in content
    assert "src/domain/" in content


def test_patterns_are_namespaced_per_language():
    paths = {d.relative_path for d in svc.preview(["java", "kotlin"], "demo", PLACEHOLDERS)}
    assert "patterns/java/use-case.md" in paths
    assert "patterns/kotlin/use-case.md" in paths
    assert "patterns/use-case.md" not in paths


def test_each_language_contributes_its_gate_script():
    paths = {d.relative_path for d in svc.preview(["java", "typescript"], "demo", PLACEHOLDERS)}
    assert "gates/scripts/verify-java.sh" in paths
    assert "gates/scripts/verify-typescript.sh" in paths


def test_gate_scripts_are_executable():
    for doc in svc.preview(["java"], "demo", PLACEHOLDERS):
        if doc.relative_path.startswith("gates/scripts/"):
            assert doc.is_executable


# -- selection --------------------------------------------------------------


def test_substitute_leaves_unknown_placeholders_visible():
    assert substitute("{{a}} and {{b}}", {"a": "x"}) == "x and {{b}}"


def test_deselecting_an_optional_rule_removes_it():
    base = base_pack()
    java = load_packs()["java"]
    optional = next(r for r in java.all_rules if not r.locked)
    values = {"project": "demo", "package": "com.example.demo"}
    defaults = base.default_rule_ids() | java.default_rule_ids()

    with_it = {d.relative_path: d for d in compose(base, [java], defaults, None, values)}
    without = {
        d.relative_path: d
        for d in compose(base, [java], defaults - {optional.id}, None, values)
    }

    owning = next(p for p, d in with_it.items() if optional.id in d.rule_ids)
    assert f"`{optional.local_id}`" in with_it[owning].content
    assert f"`{optional.local_id}`" not in without[owning].content
    assert optional.id not in without[owning].rule_ids


def test_locked_rules_survive_being_deselected():
    """The checkbox is a preference, not a permission."""
    base, languages = svc.resolve_packs(["java", "typescript"])
    packs = [base, *languages]
    locked = set().union(*(p.locked_rule_ids() for p in packs))
    assert locked

    assert locked <= svc.resolve_selection(packs, set())


def test_unknown_rule_id_is_rejected():
    base, languages = svc.resolve_packs(["java"])
    with pytest.raises(svc.InvalidSelection):
        svc.resolve_selection([base, *languages], {"no-such-rule"})


def test_required_workflows_survive_being_deselected():
    base = base_pack()
    assert set(REQUIRED_WORKFLOWS) <= svc.resolve_workflows(base, set())


def test_optional_workflows_can_be_dropped():
    paths = {
        d.relative_path
        for d in svc.preview(["java"], "demo", PLACEHOLDERS, workflow_ids=set())
    }
    for wid in REQUIRED_WORKFLOWS:
        assert f"workflows/{wid}.md" in paths
    assert "workflows/release.md" not in paths


def test_optional_workflows_are_included_when_chosen():
    paths = {
        d.relative_path
        for d in svc.preview(["java"], "demo", PLACEHOLDERS, workflow_ids={"release", "hotfix"})
    }
    assert "workflows/release.md" in paths
    assert "workflows/hotfix.md" in paths


# -- scaffolding ------------------------------------------------------------


@pytest.mark.parametrize("languages", COMBINATIONS, ids=lambda ls: "+".join(ls))
async def test_every_language_combination_scaffolds_green(store: StandardsStore, languages):
    await svc.scaffold_project(
        store, languages=languages, project="demo", placeholders=PLACEHOLDERS, actor="tester"
    )

    status = await scan_project(store, "demo")
    offenders = [
        (f.relative_path, [r.rule_id for r in f.failed])
        for f in status.files
        if f.indicator != "green"
    ]
    assert status.missing_required == []
    assert offenders == []
    assert status.indicator == "green"


async def test_scaffold_records_every_pack(store: StandardsStore):
    await svc.scaffold_project(
        store, languages=["java", "typescript"], project="demo",
        placeholders=PLACEHOLDERS, actor="tester",
    )

    project = await store.get_project("demo")
    assert project is not None
    pack_ids = {p["id"] for p in project.packs}
    assert pack_ids == {"base", "java", "typescript"}
    # The first selected language is kept for display.
    assert project.template_id == "java"
    assert project.language_version == "21"


async def test_documents_record_the_pack_that_produced_them(store: StandardsStore):
    import sqlite3

    await svc.scaffold_project(
        store, languages=["java", "typescript"], project="demo", placeholders=PLACEHOLDERS
    )

    conn = sqlite3.connect(str(store.path))
    conn.row_factory = sqlite3.Row
    rows = {
        r["relative_path"]: r["source_pack"]
        for r in conn.execute("SELECT relative_path, source_pack FROM standards_files")
    }
    conn.close()

    assert rows["core/guardrails.md"] == "base"
    assert rows["languages/java/standards.md"] == "java"
    assert rows["patterns/typescript/use-case.md"] == "typescript"


async def test_scaffold_refuses_to_overwrite(store: StandardsStore):
    await svc.scaffold_project(
        store, languages=["java"], project="demo", placeholders=PLACEHOLDERS
    )
    with pytest.raises(ProjectExists):
        await svc.scaffold_project(
            store, languages=["java"], project="demo", placeholders=PLACEHOLDERS
        )


async def test_scaffold_rejects_bad_project_name(store: StandardsStore):
    with pytest.raises(svc.InvalidProjectName):
        await svc.scaffold_project(
            store, languages=["java"], project="../etc", placeholders=PLACEHOLDERS
        )


async def test_scaffold_requires_a_language(store: StandardsStore):
    with pytest.raises(svc.NoLanguageSelected):
        await svc.scaffold_project(store, languages=[], project="demo")


async def test_scaffold_rejects_unknown_language(store: StandardsStore):
    with pytest.raises(svc.TemplateNotFound):
        await svc.scaffold_project(store, languages=["cobol"], project="demo")


async def test_scaffold_requires_declared_placeholders(store: StandardsStore):
    with pytest.raises(svc.MissingPlaceholders):
        await svc.scaffold_project(store, languages=["java"], project="demo", placeholders={})


async def test_typescript_alone_needs_no_extra_placeholders(store: StandardsStore):
    """Only the JVM packs declare `package`; a TS-only project must not ask for it."""
    assert svc.required_placeholders(svc.resolve_packs(["typescript"])[1]) == []
    await svc.scaffold_project(store, languages=["typescript"], project="tsonly")
    assert (await scan_project(store, "tsonly")).indicator == "green"


async def test_failed_scaffold_writes_nothing(store: StandardsStore):
    with pytest.raises(svc.MissingPlaceholders):
        await svc.scaffold_project(store, languages=["java"], project="demo", placeholders={})

    assert await store.list_projects() == []


async def test_source_hash_detects_a_local_edit(store: StandardsStore):
    import hashlib
    import sqlite3

    await svc.scaffold_project(
        store, languages=["java"], project="demo", placeholders=PLACEHOLDERS
    )

    conn = sqlite3.connect(str(store.path))
    conn.row_factory = sqlite3.Row
    stored = conn.execute(
        "SELECT source_hash FROM standards_files "
        "WHERE project='demo' AND relative_path='core/guardrails.md'"
    ).fetchone()["source_hash"]
    conn.close()
    assert stored

    existing = await store.get_file("demo", "core/guardrails.md")
    await store.upsert_file(
        project="demo", relative_path="core/guardrails.md", kind="markdown",
        title=existing.title, description=existing.description,
        frontmatter=existing.frontmatter, body=existing.body + "\n- locally added rule\n",
        expected_version=existing.version, updated_by="human",
    )

    after = await store.get_file("demo", "core/guardrails.md")
    edited = hashlib.sha256(
        f"---\n{after.frontmatter}\n---\n\n{after.body}".encode()
    ).hexdigest()
    assert edited != stored, "an edited document must not still match its scaffold hash"


# -- reuse boundary ---------------------------------------------------------


async def test_service_is_usable_without_the_http_layer(store: StandardsStore):
    """The guarantee that the planned MCP tool can run the same rules.

    If scaffolding ever needs a Request, this fails - which is the point.
    """
    for module in (svc, templates_store):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "starlette" not in source, f"{module.__name__} must not depend on the web layer"

    result = await svc.scaffold_project(
        store, languages=["kotlin", "typescript"], project="headless",
        placeholders=PLACEHOLDERS, actor="mcp-tool",
    )
    assert result.document_count > 0
    assert (await scan_project(store, "headless")).indicator == "green"


# -- migration --------------------------------------------------------------


async def test_provenance_columns_are_added_to_an_existing_db(tmp_path: Path):
    """A DB created before packs existed must upgrade in place."""
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE standards_projects (
            name TEXT PRIMARY KEY, indicator TEXT NOT NULL DEFAULT 'green',
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE standards_files (
            project TEXT NOT NULL, relative_path TEXT NOT NULL, kind TEXT NOT NULL,
            title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            frontmatter TEXT NOT NULL DEFAULT '', body TEXT NOT NULL,
            is_executable INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
            updated_at INTEGER NOT NULL, updated_by TEXT,
            PRIMARY KEY (project, relative_path)
        );
        INSERT INTO standards_projects VALUES ('legacy', 'green', 1);
        INSERT INTO standards_files VALUES
            ('legacy', 'AGENTS.md', 'markdown', 'Agents', '', '', 'body', 0, 1, 1, NULL);
        """
    )
    conn.commit()
    conn.close()

    s = StandardsStore(db)
    await s.init()

    project = await s.get_project("legacy")
    assert project is not None
    assert project.template_id is None  # hand-made projects simply have no provenance
    assert project.packs == []
    row = await s.get_file("legacy", "AGENTS.md")
    assert row is not None and row.body == "body"
