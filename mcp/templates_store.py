"""
templates_store.py - Load, validate and compose project template packs.

Pure: filesystem in, dataclasses and rendered markdown out. Imports nothing from
Starlette or the dashboard, so the dashboard and (later) an MCP tool can both
drive it through scaffold_service.

A project is composed from the **base pack** plus one or more **language packs**:

    base/                    always applied
      pack.yaml
      rules/*.yaml           owns core/guardrails.md, core/git.md, ARCHITECTURE.md, ...
      docs/**                AGENTS.md, INDEX.md, glossary, gates/README.md
      workflows/*.md         required and optional, flagged in their frontmatter
    languages/<id>/          one per selected language
      pack.yaml
      rules/*.yaml           contributes rules into the base-owned documents
      docs/**                languages/<id>/*, patterns/<id>/*, its gate script

Language packs contribute rather than own, because otherwise two selected
languages would both try to write core/guardrails.md. Rule ids are namespaced by
pack (`java:no-raw-types`) since several packs legitimately define a rule with
the same name.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from templates_source import find_manifests

logger = logging.getLogger(__name__)

SEVERITIES = ("hard", "soft")
MERGE_MODES = ("by-group", "by-language")

_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)

_REQUIRED_BASE_KEYS = ("id", "title", "template_version")
_REQUIRED_LANG_KEYS = ("id", "title", "language", "language_version", "template_version")


class TemplateError(Exception):
    """A pack on disk is malformed. Raised at load time, not scaffold time."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleExample:
    """A worked do/don't pair shown in the wizard's rule help popup.

    Either side may be empty - some rules only have a wrong way worth showing,
    others only a right one - but a pack that declares an example must fill in
    at least one of them.
    """

    lang: str = ""  # code-fence hint: "java", "python", "text" for pseudocode
    bad: str = ""
    good: str = ""
    caption: str = ""


@dataclass(frozen=True)
class Rule:
    id: str  # namespaced: "<pack>:<local id>"
    local_id: str
    pack: str
    group: str
    title: str
    body: str
    severity: str = "soft"
    default: bool = True
    locked: bool = False
    source: str = ""
    # Long-form help, shown only in the wizard's rule picker. Never rendered
    # into a generated document: `body` is what ships, and `source_hash`
    # provenance depends on that output not moving. Named help_text rather
    # than help so the YAML key can stay `help:` without shadowing the builtin.
    help_text: str = ""
    example: RuleExample | None = None


@dataclass(frozen=True)
class RuleGroup:
    id: str
    title: str


@dataclass
class RuleDoc:
    """A document owned by a pack and generated from togglable rules."""

    doc: str
    title: str
    description: str
    groups: list[RuleGroup]
    rules: list[Rule]
    merge: str = "by-group"
    intro: str = ""
    outro: str = ""
    tags: list[str] = field(default_factory=list)
    # Short, friendly name for this category in the wizard. Falls back to the
    # filename so a pack that omits it still renders something sensible.
    label: str = ""

    @property
    def display_label(self) -> str:
        return self.label or self.doc.rsplit("/", 1)[-1].removesuffix(".md")


@dataclass
class Contribution:
    """Rules a language pack adds to a document another pack owns."""

    contributes_to: str
    section: str
    rules: list[Rule]
    section_intro: str = ""
    section_outro: str = ""


@dataclass
class Workflow:
    id: str
    path: str
    title: str
    description: str
    content: str
    required: bool = False
    triggers: list[str] = field(default_factory=list)


@dataclass
class Pack:
    id: str
    kind: str  # "base" | "language"
    title: str
    template_version: str
    description: str
    placeholders: list[str]
    root: Path
    language: str = ""
    language_version: str = ""
    gate: str = ""
    rule_docs: list[RuleDoc] = field(default_factory=list)
    contributions: list[Contribution] = field(default_factory=list)
    plain_docs: dict[str, str] = field(default_factory=dict)
    workflows: list[Workflow] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.title} {self.language_version}".strip()

    @property
    def all_rules(self) -> list[Rule]:
        return [r for d in self.rule_docs for r in d.rules] + [
            r for c in self.contributions for r in c.rules
        ]

    def default_rule_ids(self) -> set[str]:
        return {r.id for r in self.all_rules if r.default or r.locked}

    def locked_rule_ids(self) -> set[str]:
        return {r.id for r in self.all_rules if r.locked}


@dataclass(frozen=True)
class RenderedDoc:
    relative_path: str
    content: str
    is_executable: bool
    rule_ids: list[str]
    pack: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _as_bool(value: object, default: bool) -> bool:
    return default if value is None else bool(value)


def _parse_help(value: object, local_id: str, where: Path) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TemplateError(f"{where}: rule '{local_id}' has a non-string 'help'")
    return value.strip()


def _parse_example(
    value: object, local_id: str, where: Path, default_lang: str
) -> RuleExample | None:
    """Parse a rule's optional do/don't example.

    An absent example is fine - the wizard falls back to the rule's own body -
    but a declared one that shows neither side is an authoring mistake worth
    failing the load for, since it renders as an empty popup section.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TemplateError(f"{where}: rule '{local_id}' has an 'example' that is not a mapping")

    bad = str(value.get("bad", "") or "").rstrip()
    good = str(value.get("good", "") or "").rstrip()
    if not bad and not good:
        raise TemplateError(
            f"{where}: rule '{local_id}' has an 'example' with neither 'bad' nor 'good'"
        )

    return RuleExample(
        lang=str(value.get("lang", "") or "").strip() or default_lang,
        bad=bad,
        good=good,
        caption=str(value.get("caption", "") or "").strip(),
    )


def _parse_rules(
    entries: list,
    pack_id: str,
    known_groups: set[str],
    where: Path,
    default_lang: str = "text",
) -> list[Rule]:
    rules: list[Rule] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            raise TemplateError(f"{where}: every rule must be a mapping")
        local = str(entry.get("id", "")).strip()
        if not local:
            raise TemplateError(f"{where}: a rule is missing 'id'")
        group = str(entry.get("group", "")).strip()
        if known_groups and group not in known_groups:
            raise TemplateError(f"{where}: rule '{local}' has unknown group '{group}'")
        severity = str(entry.get("severity", "soft")).strip()
        if severity not in SEVERITIES:
            raise TemplateError(
                f"{where}: rule '{local}' has severity '{severity}', expected one of {SEVERITIES}"
            )
        title = str(entry.get("title", "")).strip()
        if not title:
            raise TemplateError(f"{where}: rule '{local}' has no title")
        locked = _as_bool(entry.get("locked"), False)
        rules.append(
            Rule(
                id=f"{pack_id}:{local}",
                local_id=local,
                pack=pack_id,
                group=group,
                title=title,
                body=str(entry.get("body", "")).strip(),
                severity=severity,
                default=True if locked else _as_bool(entry.get("default"), True),
                locked=locked,
                source=str(entry.get("source", "")).strip(),
                help_text=_parse_help(entry.get("help"), local, where),
                example=_parse_example(entry.get("example"), local, where, default_lang),
            )
        )
    if not rules:
        raise TemplateError(f"{where}: no rules defined")
    return rules


def _load_rule_file(path: Path, pack_id: str, default_lang: str = "text") -> RuleDoc | Contribution:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TemplateError(f"{path}: expected a mapping at the top level")

    if raw.get("contributes_to"):
        # Groups are validated against the owning document at composition time,
        # since the owner may live in a different pack.
        return Contribution(
            contributes_to=str(raw["contributes_to"]).strip(),
            section=str(raw.get("section", pack_id)).strip(),
            rules=_parse_rules(raw.get("rules"), pack_id, set(), path, default_lang),
            section_intro=str(raw.get("section_intro", "") or ""),
            section_outro=str(raw.get("section_outro", "") or ""),
        )

    doc = str(raw.get("doc", "")).strip()
    if not doc:
        raise TemplateError(f"{path}: needs either 'doc' (to own) or 'contributes_to'")

    groups = [
        RuleGroup(id=str(g["id"]), title=str(g.get("title", g["id"])))
        for g in raw.get("groups", [])
        if isinstance(g, dict) and g.get("id")
    ]
    if not groups:
        raise TemplateError(f"{path}: at least one group is required")

    merge = str(raw.get("merge", "by-group")).strip()
    if merge not in MERGE_MODES:
        raise TemplateError(f"{path}: merge '{merge}' must be one of {MERGE_MODES}")

    return RuleDoc(
        doc=doc,
        title=str(raw.get("title", doc)),
        description=str(raw.get("description", "")),
        groups=groups,
        rules=_parse_rules(raw.get("rules"), pack_id, {g.id for g in groups}, path, default_lang),
        merge=merge,
        intro=str(raw.get("intro", "") or ""),
        outro=str(raw.get("outro", "") or ""),
        tags=[str(t) for t in raw.get("tags", [])],
        label=str(raw.get("label", "") or ""),
    )


def _load_workflows(root: Path) -> list[Workflow]:
    workflows: list[Workflow] = []
    wf_dir = root / "workflows"
    if not wf_dir.is_dir():
        return workflows
    for path in sorted(wf_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        match = _FRONTMATTER_RE.match(content)
        meta = yaml.safe_load(match.group(1)) if match else {}
        meta = meta if isinstance(meta, dict) else {}
        wid = str(meta.get("id", path.stem)).strip()
        workflows.append(
            Workflow(
                id=wid,
                path=f"workflows/{path.name}",
                title=str(meta.get("title", wid)),
                description=str(meta.get("description", "")),
                content=content,
                required=_as_bool(meta.get("required"), False),
                triggers=[str(t) for t in (meta.get("triggers") or [])],
            )
        )
    return workflows


def _load_pack(manifest_path: Path) -> Pack:
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TemplateError(f"{manifest_path}: expected a mapping at the top level")

    kind = str(raw.get("kind", "language")).strip()
    if kind not in ("base", "language"):
        raise TemplateError(f"{manifest_path}: kind must be 'base' or 'language'")

    required = _REQUIRED_BASE_KEYS if kind == "base" else _REQUIRED_LANG_KEYS
    missing = [k for k in required if not str(raw.get(k, "")).strip()]
    if missing:
        raise TemplateError(f"{manifest_path}: missing manifest keys: {', '.join(missing)}")

    root = manifest_path.parent
    pack = Pack(
        id=str(raw["id"]).strip(),
        kind=kind,
        title=str(raw["title"]).strip(),
        template_version=str(raw["template_version"]).strip(),
        description=str(raw.get("description", "")).strip(),
        placeholders=[str(p) for p in (raw.get("placeholders") or [])],
        root=root,
        language=str(raw.get("language", "")).strip(),
        language_version=str(raw.get("language_version", "")).strip(),
        gate=str(raw.get("gate", "")).strip(),
    )

    rules_dir = root / "rules"
    if rules_dir.is_dir():
        for path in sorted(rules_dir.glob("*.yaml")):
            loaded = _load_rule_file(path, pack.id, pack.language or "text")
            if isinstance(loaded, Contribution):
                pack.contributions.append(loaded)
            else:
                pack.rule_docs.append(loaded)

    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for path in sorted(docs_dir.rglob("*")):
            if path.is_file():
                pack.plain_docs[path.relative_to(docs_dir).as_posix()] = path.read_text(
                    encoding="utf-8"
                )

    pack.workflows = _load_workflows(root)
    validate(pack)
    return pack


def load_packs() -> dict[str, Pack]:
    """Every pack on the search path, keyed by id. First root wins on collision."""
    packs: dict[str, Pack] = {}
    for manifest in find_manifests():
        try:
            pack = _load_pack(manifest)
        except (TemplateError, yaml.YAMLError, OSError) as exc:
            logger.warning("Skipping pack at %s: %s", manifest, exc)
            continue
        packs.setdefault(pack.id, pack)
    return packs


def base_pack() -> Pack | None:
    return next((p for p in load_packs().values() if p.kind == "base"), None)


def language_packs() -> list[Pack]:
    packs = [p for p in load_packs().values() if p.kind == "language"]
    return sorted(packs, key=lambda p: (p.language, p.language_version))


def get_pack(pack_id: str) -> Pack | None:
    return load_packs().get(pack_id)


def validate(pack: Pack) -> None:
    """Raise TemplateError if the pack could not take part in a coherent project."""
    seen: dict[str, str] = {}
    for doc in pack.rule_docs:
        for rule in doc.rules:
            if rule.id in seen:
                raise TemplateError(
                    f"pack '{pack.id}': duplicate rule id '{rule.local_id}' "
                    f"in {doc.doc} and {seen[rule.id]}"
                )
            seen[rule.id] = doc.doc
    for contribution in pack.contributions:
        for rule in contribution.rules:
            if rule.id in seen:
                raise TemplateError(
                    f"pack '{pack.id}': duplicate rule id '{rule.local_id}' "
                    f"in {contribution.contributes_to} and {seen[rule.id]}"
                )
            seen[rule.id] = contribution.contributes_to

    owned = {d.doc for d in pack.rule_docs}
    clashes = owned & set(pack.plain_docs)
    if clashes:
        raise TemplateError(
            f"pack '{pack.id}': {', '.join(sorted(clashes))} is both generated and a plain doc"
        )

    if pack.kind == "language" and not pack.gate:
        raise TemplateError(f"pack '{pack.id}': a language pack must declare a 'gate'")


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def substitute(text: str, values: dict[str, str]) -> str:
    """Replace {{name}} with values[name]. Unknown names are left in place so a
    missing placeholder is visible in the output instead of silently blank."""
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def unresolved_placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


def _yaml_scalar(value: str) -> str:
    """Quote a frontmatter scalar when YAML would otherwise misread it.

    An unquoted ': ' silently voids the whole frontmatter block, which costs the
    document its title and description.
    """
    if value and (value[0] in "&*!|>%@`{}[]#-?" or ": " in value or value.endswith(":")):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _bullet(rule: Rule, values: dict[str, str]) -> str:
    title = substitute(rule.title, values).strip().rstrip(".")
    body = substitute(rule.body, values).strip()
    return f"- **{title}.** {body}" if body else f"- **{title}.**"


def _rule_table(rules: list[Rule], values: dict[str, str], with_scope: bool) -> list[str]:
    """A table also satisfies the scanner's structured-content rule, so every
    generated document scores green rather than amber."""
    header = "| Rule | Severity | Source |"
    divider = "| --- | --- | --- |"
    if with_scope:
        header = "| Rule | Scope | Severity | Source |"
        divider = "| --- | --- | --- | --- |"
    lines = ["## Rule reference", "", header, divider]
    for rule in rules:
        source = rule.source or "-"
        if with_scope:
            lines.append(f"| `{rule.local_id}` | {rule.pack} | {rule.severity} | {source} |")
        else:
            lines.append(f"| `{rule.local_id}` | {rule.severity} | {source} |")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


@dataclass
class _Merged:
    """One owned document plus every contribution aimed at it."""

    owner: Pack
    doc: RuleDoc
    contributions: list[tuple[Pack, Contribution]] = field(default_factory=list)


def _collect(base: Pack, languages: list[Pack]) -> dict[str, _Merged]:
    merged: dict[str, _Merged] = {}
    for pack in [base, *languages]:
        for doc in pack.rule_docs:
            if doc.doc in merged:
                raise TemplateError(
                    f"packs '{merged[doc.doc].owner.id}' and '{pack.id}' both own {doc.doc}"
                )
            merged[doc.doc] = _Merged(owner=pack, doc=doc)

    for pack in languages:
        for contribution in pack.contributions:
            target = merged.get(contribution.contributes_to)
            if target is None:
                raise TemplateError(
                    f"pack '{pack.id}' contributes to {contribution.contributes_to}, "
                    "which no selected pack owns"
                )
            known = {g.id for g in target.doc.groups}
            for rule in contribution.rules:
                if rule.group not in known:
                    raise TemplateError(
                        f"pack '{pack.id}': rule '{rule.local_id}' targets group "
                        f"'{rule.group}', not declared by {contribution.contributes_to}"
                    )
            target.contributions.append((pack, contribution))
    return merged


def _render_by_group(m: _Merged, selected: set[str], values: dict[str, str]) -> str:
    """Groups outermost, one subsection per contributing language.

    With a single language the subheadings are dropped, so a single-language
    project reads exactly as it did before packs existed.
    """
    doc = m.doc
    title = substitute(doc.title, values)
    lines = [
        "---",
        f"title: {_yaml_scalar(title)}",
        f"description: {_yaml_scalar(substitute(doc.description, values))}",
    ]
    if doc.tags:
        lines.append(f"tags: [{', '.join(doc.tags)}]")
    lines += ["---", "", f"# {title}", ""]

    intro = substitute(doc.intro, values).strip()
    if intro:
        lines += [intro, ""]

    contributing = [(p, c) for p, c in m.contributions if any(r.id in selected for r in c.rules)]
    label_sections = len(contributing) > 1
    used: list[Rule] = []

    for group in doc.groups:
        own = [r for r in doc.rules if r.group == group.id and r.id in selected]
        per_section = [
            (c.section, [r for r in c.rules if r.group == group.id and r.id in selected])
            for _, c in contributing
        ]
        per_section = [(s, rs) for s, rs in per_section if rs]
        if not own and not per_section:
            continue

        lines += [f"## {group.title}", ""]
        for rule in own:
            lines.append(_bullet(rule, values))
            used.append(rule)
        if own:
            lines.append("")
        for section, rules in per_section:
            if label_sections:
                lines += [f"### {section}", ""]
            for rule in rules:
                lines.append(_bullet(rule, values))
                used.append(rule)
            lines.append("")

    lines += _rule_table(used, values, with_scope=label_sections)

    outro = substitute(doc.outro, values).strip()
    if outro:
        lines += [outro, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_by_language(m: _Merged, selected: set[str], values: dict[str, str]) -> str:
    """Language outermost - the right shape when the content itself is
    per-language, as a module layout is."""
    doc = m.doc
    title = substitute(doc.title, values)
    lines = [
        "---",
        f"title: {_yaml_scalar(title)}",
        f"description: {_yaml_scalar(substitute(doc.description, values))}",
    ]
    if doc.tags:
        lines.append(f"tags: [{', '.join(doc.tags)}]")
    lines += ["---", "", f"# {title}", ""]

    intro = substitute(doc.intro, values).strip()
    if intro:
        lines += [intro, ""]

    used: list[Rule] = []

    own = [r for r in doc.rules if r.id in selected]
    if own:
        for group in doc.groups:
            in_group = [r for r in own if r.group == group.id]
            if not in_group:
                continue
            lines += [f"## {group.title}", ""]
            for rule in in_group:
                lines.append(_bullet(rule, values))
                used.append(rule)
            lines.append("")

    for _pack, contribution in m.contributions:
        rules = [r for r in contribution.rules if r.id in selected]
        section_intro = substitute(contribution.section_intro, values).strip()
        if not rules and not section_intro:
            continue
        lines += [f"## {contribution.section}", ""]
        if section_intro:
            lines += [section_intro, ""]
        for group in doc.groups:
            in_group = [r for r in rules if r.group == group.id]
            if not in_group:
                continue
            lines += [f"### {group.title}", ""]
            for rule in in_group:
                lines.append(_bullet(rule, values))
                used.append(rule)
            lines.append("")
        section_outro = substitute(contribution.section_outro, values).strip()
        if section_outro:
            lines += [section_outro, ""]

    lines += _rule_table(used, values, with_scope=len(m.contributions) > 1)

    outro = substitute(doc.outro, values).strip()
    if outro:
        lines += [outro, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def computed_values(base: Pack, languages: list[Pack]) -> dict[str, str]:
    """Placeholders derived from the selection rather than supplied by the user."""
    gates = "\n".join(f"bash {p.gate}" for p in languages)
    language_rows = "\n".join(
        f"| {p.title} {p.language_version} | `#{p.title.lower()}` |" for p in languages
    )
    doc_rows = "\n".join(
        f"| {p.title} | `languages/{p.language}/standards.md`, "
        f"`languages/{p.language}/testing.md`, "
        f"`languages/{p.language}/anti-patterns.md` |"
        for p in languages
    )
    return {
        "languages": ", ".join(p.label for p in languages),
        "gates": gates,
        "language_rows": language_rows,
        "language_doc_rows": doc_rows,
    }


def compose(
    base: Pack,
    languages: list[Pack],
    selected_rule_ids: set[str] | None = None,
    workflow_ids: set[str] | None = None,
    placeholders: dict[str, str] | None = None,
) -> list[RenderedDoc]:
    """Render every document the selection produces.

    `selected_rule_ids` of None means the defaults. Locked rules and required
    workflows are forced on regardless of what the caller passed - the toggle is
    a preference, not a permission.
    """
    packs = [base, *languages]
    values = dict(placeholders or {})
    values.update(computed_values(base, languages))

    if selected_rule_ids is None:
        selected = set().union(*(p.default_rule_ids() for p in packs)) if packs else set()
    else:
        selected = set(selected_rule_ids)
    selected |= set().union(*(p.locked_rule_ids() for p in packs)) if packs else set()

    required_workflows = {w.id for w in base.workflows if w.required}
    if workflow_ids is None:
        chosen_workflows = {w.id for w in base.workflows}
    else:
        chosen_workflows = set(workflow_ids) | required_workflows

    # INDEX.md lists the workflows this project actually has, so the table can
    # only be built once the selection is known.
    rows = [
        f"| {', '.join(w.triggers) or w.title} | `{w.path}` |"
        for w in base.workflows
        if w.id in chosen_workflows
    ]
    values["workflow_rows"] = "\n".join(["| Trigger phrase | Document |", "| --- | --- |", *rows])

    merged = _collect(base, languages)
    out: list[RenderedDoc] = []

    for path, m in merged.items():
        render_fn = _render_by_language if m.doc.merge == "by-language" else _render_by_group
        content = render_fn(m, selected, values)
        rule_ids = [r.id for r in m.doc.rules if r.id in selected]
        for _, contribution in m.contributions:
            rule_ids += [r.id for r in contribution.rules if r.id in selected]
        out.append(
            RenderedDoc(
                relative_path=path,
                content=content,
                is_executable=False,
                rule_ids=rule_ids,
                pack=m.owner.id,
            )
        )

    seen_paths = {d.relative_path for d in out}
    for pack in packs:
        for rel, content in pack.plain_docs.items():
            if rel in seen_paths:
                raise TemplateError(f"pack '{pack.id}' would overwrite {rel}")
            seen_paths.add(rel)
            out.append(
                RenderedDoc(
                    relative_path=rel,
                    content=substitute(content, values),
                    is_executable=rel.startswith("gates/scripts/") and rel.endswith(".sh"),
                    rule_ids=[],
                    pack=pack.id,
                )
            )

    for workflow in base.workflows:
        if workflow.id not in chosen_workflows:
            continue
        out.append(
            RenderedDoc(
                relative_path=workflow.path,
                content=substitute(workflow.content, values),
                is_executable=False,
                rule_ids=[],
                pack=base.id,
            )
        )

    out.sort(key=lambda d: d.relative_path)
    return out


__all__ = [
    "Contribution",
    "MERGE_MODES",
    "Pack",
    "RenderedDoc",
    "Rule",
    "RuleDoc",
    "RuleGroup",
    "TemplateError",
    "Workflow",
    "base_pack",
    "compose",
    "computed_values",
    "get_pack",
    "language_packs",
    "load_packs",
    "substitute",
    "unresolved_placeholders",
    "validate",
]
