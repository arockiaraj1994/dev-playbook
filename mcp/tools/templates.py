"""playbook_list_templates - the language pack catalog.

Split out of playbook_scaffold_standards on purpose. The catalog is discovery
data: folding it into the scaffold tool's description would spend those tokens
in every conversation, whether or not anyone scaffolds anything. As its own
tool it is a round-trip paid only when a project actually needs bootstrapping.
"""

from __future__ import annotations

from mcp.types import TextContent, Tool

import scaffold_service
from standards_store import StandardsStore
from tools.common import READ_ONLY, as_str, error, text

NAME = "playbook_list_templates"

DEFINITIONS: list[Tool] = [
    Tool(
        name=NAME,
        title="List standards templates",
        description=(
            "List the language template packs available for scaffolding a new "
            "standards project, with the rule groups and placeholders each one "
            "needs. Call this before playbook_scaffold_standards so you pass "
            "language ids that exist.\n\n"
            'Example: a user says "set up coding standards for this Spring Boot '
            'service" - call playbook_list_templates(), then '
            'playbook_scaffold_standards(project="billing", languages=["java"], '
            "dry_run=true) to show them what it would generate.\n\n"
            "Does not use: reading an existing project's standards (that is "
            "playbook_get_standard or playbook_find_standards)."
        ),
        annotations=READ_ONLY,
        inputSchema={
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": (
                        "Optional. Show the full rule and workflow detail for one "
                        "pack id (e.g. 'java'). Omitted, returns the summary of "
                        "every pack."
                    ),
                }
            },
            "required": [],
        },
    )
]


def _summary() -> str:
    packs = scaffold_service.list_languages()
    if not packs:
        return (
            "No language packs are installed on this server. Scaffolding is "
            "unavailable until at least one pack is present under the template "
            "search path."
        )

    base = scaffold_service.get_base()
    lines = ["# Standards templates", ""]
    lines.append(
        f"Every project also gets the **{base.title}** base pack "
        f"(v{base.template_version}): {base.description.strip()}"
    )
    lines.append("")
    lines.append("| id | language | version | rules | needs |")
    lines.append("| --- | --- | --- | --- | --- |")
    for pack in packs:
        needs = [p for p in pack.placeholders if p != "project"]
        lines.append(
            f"| `{pack.id}` | {pack.title} | {pack.language_version or '-'} | "
            f"{len(pack.all_rules)} | {', '.join(needs) if needs else '-'} |"
        )
    lines += [
        "",
        f"Base workflows included in every project: {', '.join(w.id for w in base.workflows)}.",
        "",
        "## Next Calls",
        "",
        '- Detail for one pack: `playbook_list_templates(language="<id>")`',
        '- Preview a project: `playbook_scaffold_standards(project="<name>", '
        'languages=["<id>"], dry_run=true)`',
    ]
    return "\n".join(lines)


def _detail(language: str) -> str:
    try:
        _base, packs = scaffold_service.resolve_packs([language])
    except scaffold_service.ScaffoldError:
        known = ", ".join(p.id for p in scaffold_service.list_languages())
        return (
            f"No language pack with id '{language}'.\n"
            f"Available ids: {known or '(none installed)'}\n\n"
            "Call playbook_list_templates() with no argument for the full catalog."
        )

    pack = packs[0]
    lines = [
        f"# {pack.title} {pack.language_version}".rstrip(),
        "",
        f"Pack id `{pack.id}`, template version {pack.template_version}.",
        "",
        pack.description.strip(),
        "",
    ]

    needs = [p for p in pack.placeholders if p != "project"]
    if needs:
        lines += [
            "## Required placeholders",
            "",
            "You must supply these in `placeholders`, or scaffolding fails:",
            "",
            *[f"- `{n}`" for n in needs],
            "",
        ]

    lines += ["## Rules", ""]
    for doc in pack.rule_docs:
        lines.append(f"### {doc.display_label} - writes `{doc.doc}`")
        lines.append("")
        for rule in doc.rules:
            flags = []
            if rule.locked:
                flags.append("locked")
            elif not rule.default:
                flags.append("off by default")
            suffix = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"- `{rule.id}` - {rule.title} [{rule.severity}]{suffix}")
        lines.append("")

    for contrib in pack.contributions:
        lines.append(f"### {contrib.section} - contributed into `{contrib.contributes_to}`")
        lines.append("")
        for rule in contrib.rules:
            lines.append(f"- `{rule.id}` - {rule.title} [{rule.severity}]")
        lines.append("")

    if pack.gate:
        lines += [f"Ships a gate script: `{pack.gate}`.", ""]

    lines += [
        "Locked rules are always included whatever you pass in `rule_ids`. "
        "Omit `rule_ids` entirely to take the pack defaults, which is the normal "
        "non-interactive choice.",
        "",
        "## Next Calls",
        "",
        f'- Preview: `playbook_scaffold_standards(project="<name>", '
        f'languages=["{pack.id}"], dry_run=true)`',
    ]
    return "\n".join(lines)


async def dispatch(
    name: str, arguments: dict, ctx, store: StandardsStore
) -> list[TextContent] | None:
    if name != NAME:
        return None
    language = as_str(arguments.get("language"))
    if language:
        body = _detail(language)
        if body.startswith("No language pack"):
            ctx.status = "error"
            return error(body)
        return text(body)
    return text(_summary())


__all__ = ["DEFINITIONS", "NAME", "dispatch"]
