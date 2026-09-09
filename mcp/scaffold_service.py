"""
scaffold_service.py - Create a standards project from the template packs.

The single entry point for scaffolding. The dashboard wizard calls it today; the
planned MCP tool that bootstraps a project from an existing codebase will call
the same function, so both paths apply exactly the same rules.

Nothing here knows about HTTP. Failures raise typed errors and each caller maps
them to its own transport: status codes for the dashboard, tool errors for MCP.
"""

from __future__ import annotations

import logging
import re

from standards_scanner import _extract_description, _extract_title, _parse_frontmatter
from standards_store import StandardsStore, _frontmatter_text
from templates_store import (
    Pack,
    RenderedDoc,
    base_pack,
    compose,
    language_packs,
    load_packs,
    unresolved_placeholders,
)

logger = logging.getLogger(__name__)

# Same shape the dashboard enforces for project names in URLs.
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ScaffoldError(Exception):
    """Base class so a caller can catch every scaffold failure at once."""


class TemplateNotFound(ScaffoldError):
    def __init__(self, pack_id: str) -> None:
        self.pack_id = pack_id
        super().__init__(f"no language pack with id '{pack_id}'")


class NoBasePack(ScaffoldError):
    def __init__(self) -> None:
        super().__init__("no base pack found; templates/base/pack.yaml is missing or malformed")


class NoLanguageSelected(ScaffoldError):
    def __init__(self) -> None:
        super().__init__("select at least one language")


class InvalidProjectName(ScaffoldError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"invalid project name '{name}': use letters, digits, dot, dash or underscore"
        )


class InvalidSelection(ScaffoldError):
    def __init__(self, unknown: set[str]) -> None:
        self.unknown = unknown
        super().__init__(f"unknown rule ids: {', '.join(sorted(unknown))}")


class MissingPlaceholders(ScaffoldError):
    def __init__(self, names: set[str]) -> None:
        self.names = names
        super().__init__(f"missing values for placeholders: {', '.join(sorted(names))}")


class ScaffoldResult:
    def __init__(self, project: str, packs: list[Pack], documents: list[RenderedDoc]) -> None:
        self.project = project
        self.packs = packs
        self.documents = documents

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def languages(self) -> list[Pack]:
        return [p for p in self.packs if p.kind == "language"]


def is_valid_project_name(name: str) -> bool:
    """Shared with the dashboard so the form and the service agree on what is legal."""
    return bool(_PROJECT_NAME_RE.match(name or ""))


def list_languages() -> list[Pack]:
    """Every selectable language pack, ordered for display."""
    return language_packs()


def get_base() -> Pack:
    base = base_pack()
    if base is None:
        raise NoBasePack()
    return base


def resolve_packs(language_ids: list[str]) -> tuple[Pack, list[Pack]]:
    if not language_ids:
        raise NoLanguageSelected()
    packs = load_packs()
    selected: list[Pack] = []
    for lang_id in language_ids:
        pack = packs.get(lang_id)
        if pack is None or pack.kind != "language":
            raise TemplateNotFound(lang_id)
        selected.append(pack)
    return get_base(), selected


def required_placeholders(languages: list[Pack]) -> list[str]:
    """Placeholder names the user must supply for this language selection."""
    names: list[str] = []
    for pack in languages:
        for name in pack.placeholders:
            if name != "project" and name not in names:
                names.append(name)
    return names


def resolve_selection(packs: list[Pack], selected_rule_ids: set[str] | None) -> set[str]:
    """Validate a rule selection and force locked rules on.

    None means "the defaults", which is what a non-interactive caller such as an
    MCP tool will normally want.
    """
    locked: set[str] = set()
    known: set[str] = set()
    defaults: set[str] = set()
    for pack in packs:
        known |= {r.id for r in pack.all_rules}
        locked |= pack.locked_rule_ids()
        defaults |= pack.default_rule_ids()

    if selected_rule_ids is None:
        return defaults

    unknown = set(selected_rule_ids) - known
    if unknown:
        raise InvalidSelection(unknown)
    # Locked rules are not negotiable, whatever the caller sent.
    return set(selected_rule_ids) | locked


def resolve_workflows(base: Pack, workflow_ids: set[str] | None) -> set[str]:
    required = {w.id for w in base.workflows if w.required}
    if workflow_ids is None:
        return {w.id for w in base.workflows}
    return set(workflow_ids) | required


def _to_row(doc: RenderedDoc) -> dict:
    """Convert a rendered document into the column shape standards_files wants.

    Uses the same helpers as the JSON seeder so scaffolded rows carry titles and
    descriptions identical in shape to every other row in the store.
    """
    if doc.relative_path.endswith(".md"):
        meta, body = _parse_frontmatter(doc.content)
        title = _extract_title(meta, body, doc.relative_path.rsplit("/", 1)[-1])
        description = _extract_description(meta)
        frontmatter = _frontmatter_text(doc.content)
        kind = "markdown"
    else:
        body = doc.content
        title = doc.relative_path.rsplit("/", 1)[-1]
        description = ""
        frontmatter = ""
        kind = "script"

    return {
        "relative_path": doc.relative_path,
        "kind": kind,
        "title": title,
        "description": description,
        "frontmatter": frontmatter,
        "body": body,
        "is_executable": doc.is_executable,
        "source_hash": doc.sha256,
        "source_rule_ids": doc.rule_ids,
        "source_pack": doc.pack,
    }


def _placeholder_values(
    base: Pack, languages: list[Pack], project: str, placeholders: dict[str, str] | None
) -> dict[str, str]:
    values = {k: v for k, v in (placeholders or {}).items() if v}
    values.setdefault("project", project)
    missing = {p for p in required_placeholders(languages) if not values.get(p)}
    if missing:
        raise MissingPlaceholders(missing)
    return values


def preview(
    language_ids: list[str],
    project: str,
    placeholders: dict[str, str] | None = None,
    selected_rule_ids: set[str] | None = None,
    workflow_ids: set[str] | None = None,
) -> list[RenderedDoc]:
    """Render without writing. Used by tests, the wizard's review step, and any
    caller wanting a dry run."""
    base, languages = resolve_packs(language_ids)
    values = _placeholder_values(base, languages, project, placeholders)
    return compose(
        base,
        languages,
        resolve_selection([base, *languages], selected_rule_ids),
        resolve_workflows(base, workflow_ids),
        values,
    )


async def scaffold_project(
    store: StandardsStore,
    *,
    languages: list[str],
    project: str,
    placeholders: dict[str, str] | None = None,
    selected_rule_ids: set[str] | None = None,
    workflow_ids: set[str] | None = None,
    actor: str | None = None,
) -> ScaffoldResult:
    """Create `project` from the base pack plus `languages`, writing every document.

    Raises InvalidProjectName, NoLanguageSelected, TemplateNotFound,
    MissingPlaceholders, InvalidSelection or ProjectExists. Nothing is written
    unless everything validates - the write itself is a single transaction.
    """
    if not is_valid_project_name(project):
        raise InvalidProjectName(project)

    base, language_list = resolve_packs(languages)
    values = _placeholder_values(base, language_list, project, placeholders)
    selection = resolve_selection([base, *language_list], selected_rule_ids)
    workflows = resolve_workflows(base, workflow_ids)
    documents = compose(base, language_list, selection, workflows, values)

    # Catch a pack that references a placeholder it never declared, rather than
    # writing "{{package}}" into someone's standards.
    leftover: set[str] = set()
    for doc in documents:
        leftover |= unresolved_placeholders(doc.content)
    if leftover:
        raise MissingPlaceholders(leftover)

    packs = [base, *language_list]
    primary = language_list[0]

    await store.create_project_from_template(
        project=project,
        template_id=primary.id,
        template_version=primary.template_version,
        language=primary.language,
        language_version=primary.language_version,
        packs=[
            {"id": p.id, "kind": p.kind, "version": p.template_version} for p in packs
        ],
        documents=[_to_row(doc) for doc in documents],
        updated_by=actor,
    )

    return ScaffoldResult(project=project, packs=packs, documents=documents)


__all__ = [
    "InvalidProjectName",
    "InvalidSelection",
    "MissingPlaceholders",
    "NoBasePack",
    "NoLanguageSelected",
    "ScaffoldError",
    "ScaffoldResult",
    "TemplateNotFound",
    "get_base",
    "is_valid_project_name",
    "list_languages",
    "preview",
    "required_placeholders",
    "resolve_packs",
    "resolve_selection",
    "resolve_workflows",
    "scaffold_project",
]
