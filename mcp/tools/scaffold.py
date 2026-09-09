"""playbook_scaffold_standards - bootstrap a standards project for a codebase.

This module contains no scaffolding logic. It parses arguments, checks
authorization, calls scaffold_service, and formats the result. That is
deliberate: the dashboard wizard calls the same two functions, so the two paths
cannot drift into applying different rules. A parity test diffs the store dump
produced by each.
"""

from __future__ import annotations

import logging

from mcp.types import TextContent, Tool

import scaffold_service
from identity import principal_var
from standards_store import ProjectExists, StandardsStore
from templates_store import RenderedDoc
from tools.common import (
    POLICY,
    WRITE_ADDITIVE,
    as_bool,
    as_str,
    as_str_list,
    as_str_map,
    as_str_set,
    error,
    text,
)

logger = logging.getLogger(__name__)

NAME = "playbook_scaffold_standards"

DEFINITIONS: list[Tool] = [
    Tool(
        name=NAME,
        title="Create standards for a project",
        description=(
            "Create a new standards project for an existing codebase that does "
            "not have one yet: guardrails, definition of done, git practice, "
            "per-language rules and the workflow documents, composed from the "
            "template packs. Call playbook_list_templates first to get valid "
            "language ids and to learn which placeholders a pack requires.\n\n"
            "Run it with dry_run=true first and show the user the manifest. "
            "Only call it with dry_run=false once they have agreed - it writes.\n\n"
            "Example: the repo is a Java service in package com.acme.billing, so "
            'call playbook_scaffold_standards(project="billing", languages=["java"], '
            'placeholders={"package": "com.acme.billing"}, dry_run=true).\n\n'
            "Limitations: it creates a project, it never updates one. Scaffolding "
            "over an existing project fails rather than merging. Omit rule_ids and "
            "workflow_ids to take the pack defaults, which is normally what you "
            "want; rules marked locked are always included either way."
        ),
        annotations=WRITE_ADDITIVE,
        inputSchema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": (
                        "Name for the new standards project, usually the repository "
                        "name. Letters, digits, dot, dash or underscore; max 64 chars."
                    ),
                },
                "languages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "One or more language pack ids from playbook_list_templates, "
                        'e.g. ["java"] or ["typescript", "python"] for a repo '
                        "with both. At least one is required."
                    ),
                },
                "placeholders": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": (
                        "Values the selected packs require, e.g. "
                        '{"package": "com.acme.billing"}. playbook_list_templates '
                        "names them per pack. 'project' is filled in for you."
                    ),
                },
                "rule_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional. Namespaced rule ids to include, e.g. "
                        '"java:ap-float-for-money". Omit for the pack defaults.'
                    ),
                },
                "workflow_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        'Optional. Workflow ids to include, e.g. ["bug-fix", '
                        '"new-feature"]. Omit for all of them. Required workflows '
                        "are included regardless."
                    ),
                },
                "dry_run": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "True renders the documents and returns a manifest without "
                        "writing anything. Do this first."
                    ),
                },
            },
            "required": ["project", "languages"],
        },
    )
]


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def _authorize() -> str | None:
    """Return refusal text, or None when the call may proceed."""
    if not POLICY.scaffold_enabled:
        return (
            "Scaffolding is disabled on this server ([enable] scaffold = false in "
            "config.toml). An administrator has to turn it on; the read tools "
            "(playbook_get_standard, playbook_find_standards) still work."
        )
    if not POLICY.auth_enabled:
        # Auth off means a single trusted local operator - there is no role to
        # check, and gating on one would lock the tool out entirely.
        return None
    principal = principal_var.get()
    role = principal.role if principal else ""
    if role != "admin":
        return (
            "Creating standards requires an admin token on this server. Your token "
            f"has role '{role or 'unknown'}'. Ask an administrator to scaffold the "
            "project from the dashboard, or to issue you an admin token. The read "
            "tools are unaffected."
        )
    return None


# ---------------------------------------------------------------------------
# Error mapping
#
# Each typed error becomes text that says what to do next. A model that gets
# "unknown rule ids: {...}" and nothing else has no move; one that gets told to
# call playbook_list_templates does.
# ---------------------------------------------------------------------------


def _known_language_ids() -> str:
    ids = [p.id for p in scaffold_service.list_languages()]
    return ", ".join(ids) if ids else "(no packs installed)"


def _explain(exc: Exception, project: str) -> str:
    if isinstance(exc, scaffold_service.NoLanguageSelected):
        return (
            "No language selected. Pass at least one pack id in `languages`.\n"
            f"Available: {_known_language_ids()}\n\n"
            "Call playbook_list_templates() to see what each one covers."
        )
    if isinstance(exc, scaffold_service.TemplateNotFound):
        return (
            f"No language pack with id '{exc.pack_id}'.\n"
            f"Available: {_known_language_ids()}\n\n"
            "Call playbook_list_templates() and retry with an id from that list."
        )
    if isinstance(exc, scaffold_service.InvalidProjectName):
        return (
            f"'{exc.name}' is not a usable project name. Use letters, digits, dot, "
            "dash or underscore, starting with a letter or digit, at most 64 "
            "characters. The repository name usually works as-is."
        )
    if isinstance(exc, scaffold_service.MissingPlaceholders):
        names = ", ".join(sorted(exc.names))
        return (
            f"The selected packs need values you did not supply: {names}.\n\n"
            "These come from the codebase - for example a Java pack's `package` is "
            "the service's root package. Read it from the source, then call again "
            'with placeholders={"<name>": "<value>"}. '
            'playbook_list_templates(language="<id>") lists what each pack needs.'
        )
    if isinstance(exc, scaffold_service.InvalidSelection):
        unknown = ", ".join(sorted(exc.unknown))
        return (
            f"Unknown rule ids: {unknown}.\n\n"
            "Rule ids are namespaced by pack, e.g. 'java:ap-float-for-money'. "
            'Call playbook_list_templates(language="<id>") for the full list, or '
            "omit `rule_ids` entirely to take the pack defaults."
        )
    if isinstance(exc, scaffold_service.NoBasePack):
        return (
            "This server has no base template pack installed, so nothing can be "
            "scaffolded. That is a server misconfiguration - report it to an "
            "administrator."
        )
    if isinstance(exc, ProjectExists):
        return (
            f"A standards project named '{project}' already exists. Scaffolding "
            "creates projects and never merges into one, so this call was refused "
            "and nothing changed.\n\n"
            f'Read what it already contains: playbook_find_standards(project="{project}") '
            "with no query lists every document. Editing an existing project is done "
            "from the dashboard."
        )
    return f"Scaffolding failed: {exc}"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _manifest(docs: list[RenderedDoc]) -> list[str]:
    lines = ["| document | rules | from | size |", "| --- | --- | --- | --- |"]
    for doc in sorted(docs, key=lambda d: d.relative_path):
        lines.append(
            f"| `{doc.relative_path}` | {len(doc.rule_ids)} | {doc.pack} | {len(doc.content)} B |"
        )
    return lines


def _preview_body(project: str, languages: list[str], docs: list[RenderedDoc]) -> str:
    total_rules = sum(len(d.rule_ids) for d in docs)
    return "\n".join(
        [
            f"# Preview - standards for '{project}'",
            "",
            f"**Nothing was written.** {len(docs)} documents, {total_rules} rules, "
            f"from base + {', '.join(languages)}.",
            "",
            *_manifest(docs),
            "",
            "## Next Calls",
            "",
            "- Show this manifest to the user. If they agree, call the same tool "
            "again with `dry_run=false`.",
        ]
    )


def _created_body(project: str, result: scaffold_service.ScaffoldResult) -> str:
    langs = ", ".join(f"{p.title} {p.language_version}".strip() for p in result.languages)
    return "\n".join(
        [
            f"# Created standards project '{project}'",
            "",
            f"{result.document_count} documents written, from base + {langs}.",
            "",
            *_manifest(result.documents),
            "",
            "## Next Calls",
            "",
            f'- Start work under them: `playbook_start_task(project="{project}", '
            'intent="<what you are about to do>")`',
            f'- Read the guardrails: `playbook_get_standard(project="{project}", '
            'ref="guardrails")`',
            "",
            "Tell the user the project was created and that they can edit it from "
            "the dashboard's Standards page.",
        ]
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def dispatch(
    name: str, arguments: dict, ctx, store: StandardsStore
) -> list[TextContent] | None:
    if name != NAME:
        return None

    project = as_str(arguments.get("project"))
    languages = as_str_list(arguments.get("languages"))
    dry_run = as_bool(arguments.get("dry_run"), False)
    ctx.doc_path = project

    refusal = _authorize()
    if refusal:
        ctx.status = "error"
        return error(refusal)

    kwargs = {
        "placeholders": as_str_map(arguments.get("placeholders")),
        "selected_rule_ids": as_str_set(arguments.get("rule_ids")),
        "workflow_ids": as_str_set(arguments.get("workflow_ids")),
    }

    if dry_run:
        try:
            docs = scaffold_service.preview(languages, project, **kwargs)
        except scaffold_service.ScaffoldError as exc:
            ctx.status = "error"
            return error(_explain(exc, project))
        return text(_preview_body(project, languages, docs))

    principal = principal_var.get()
    try:
        result = await scaffold_service.scaffold_project(
            store,
            languages=languages,
            project=project,
            actor=principal.user_name if principal else None,
            **kwargs,
        )
    except (scaffold_service.ScaffoldError, ProjectExists) as exc:
        ctx.status = "error"
        return error(_explain(exc, project))

    logger.info("scaffolded project=%s docs=%d", project, result.document_count)
    return text(_created_body(project, result))


__all__ = ["DEFINITIONS", "NAME", "dispatch"]
