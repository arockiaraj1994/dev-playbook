"""refs.py - the `ref` doc-address grammar.

Top-level (not under tools/) so scripts/validate-rules.py can import it
without pulling in the MCP SDK: the CI validator and the server must agree on
what a valid ref is, and that agreement is this file.

A `ref` names one doc in one string, using exactly the vocabulary the corpus
already speaks in `see_also:` / `targets:` frontmatter:

    agents                      AGENTS.md
    guardrails                  core/guardrails.md + core/definition-of-done.md
    architecture                architecture/overview.md
    architecture:<slug>         architecture/decisions/<slug>.md
    language:<lang>             languages/<lang>/standards.md
    language:<lang>/<section>   languages/<lang>/<section>.md
    pattern:<name>              patterns/<name>.md
    skill:<name>                skills/<name>.md
    workflow:<name>             workflows/<name>.md
    gate                        gates/README.md
    gate:<script>               gates/scripts/<script>.sh

Before v0.8.0 this grammar existed only in frontmatter and had to be translated
into `kind=` / `name=` / `section=` / `depth=` arguments by a ~100-line switch.
`playbook_get(ref=...)` accepts it directly, so a `see_also:` entry and the call
that follows it are the same string.

This module is the single definition of the grammar: the tools parse and render
refs with it, and scripts/validate-rules.py validates frontmatter with it.
"""

from __future__ import annotations

from dataclasses import dataclass

# Kinds addressable by a ref, in the order they are documented.
REF_KINDS: tuple[str, ...] = (
    "agents",
    "guardrails",
    "architecture",
    "language",
    "pattern",
    "skill",
    "workflow",
    "gate",
)

# Kinds that never take a name.
_SINGLETON_KINDS = frozenset({"agents", "guardrails"})
# Kinds where a name is optional (bare ref selects the overview / README).
_OPTIONAL_NAME_KINDS = frozenset({"architecture", "gate"})
# Kinds that require a name.
_REQUIRED_NAME_KINDS = frozenset({"language", "pattern", "skill", "workflow"})

LANGUAGE_SECTIONS: tuple[str, ...] = ("standards", "testing", "anti-patterns")

# Legacy frontmatter spellings that mean the same doc. Kept so existing
# `see_also:` entries keep resolving - these are doc kinds, not tool names, and
# were never part of the v0.8.0 tool-name break.
_KIND_ALIASES: dict[str, str] = {
    "gates": "gate",
    "core": "guardrails",
}

# `core:<name>` entries all collapse onto the guardrails bundle.
_CORE_NAMES = frozenset({"guardrails", "definition-of-done"})

# Names that mean "no name" for the optional-name kinds.
_EMPTY_NAMES = frozenset({"", "overview", "readme", "gate"})


class RefError(ValueError):
    """A ref that cannot be parsed. The message is shown to the agent."""


@dataclass(frozen=True)
class Ref:
    """A parsed doc address."""

    kind: str
    name: str = ""
    section: str = ""  # language only

    def __str__(self) -> str:
        if self.kind == "language":
            return f"language:{self.name}/{self.section}"
        if self.name:
            return f"{self.kind}:{self.name}"
        return self.kind

    @property
    def label(self) -> str:
        """Short human label used in Next Calls bullets."""
        if self.kind == "agents":
            return "AGENTS.md"
        if self.kind == "guardrails":
            return "always-on rules"
        if self.kind == "architecture":
            return f"ADR `{self.name}`" if self.name else "architecture overview"
        if self.kind == "language":
            return f"{self.name} {self.section}"
        if self.kind == "gate":
            return f"gate `{self.name}`" if self.name else "gate README"
        return f"{self.kind} `{self.name}`"


def parse_ref(raw: str) -> Ref:
    """Parse a ref string. Raises RefError with a teaching message."""
    text = (raw or "").strip()
    if not text:
        raise RefError(
            '`ref` is required. It names one doc, e.g. "guardrails", '
            '"pattern:repository", "language:kotlin/testing". '
            f"Kinds: {', '.join(REF_KINDS)}."
        )

    kind, sep, name = text.partition(":")
    kind = _KIND_ALIASES.get(kind.strip().lower(), kind.strip().lower())
    name = name.strip()

    # `core:definition-of-done` and friends resolve to the guardrails bundle.
    if sep and kind == "guardrails":
        if name and name.lower() not in _CORE_NAMES:
            raise RefError(
                f"Unknown core doc '{name}'. Use \"guardrails\" - it returns both "
                "core/guardrails.md and core/definition-of-done.md."
            )
        name = ""

    if kind not in REF_KINDS:
        raise RefError(
            f"Unknown ref kind '{kind}'. Expected one of: {', '.join(REF_KINDS)}. "
            f'Got ref="{text}".'
        )

    if kind in _SINGLETON_KINDS:
        if name:
            raise RefError(f'ref "{kind}" takes no name; got "{text}".')
        return Ref(kind)

    if kind in _OPTIONAL_NAME_KINDS:
        if name.lower() in _EMPTY_NAMES:
            name = ""
        return Ref(kind, name)

    # Required-name kinds from here down.
    if not name:
        raise RefError(
            f'ref "{kind}" needs a name, e.g. "{kind}:{_example_name(kind)}". Got ref="{text}".'
        )

    if kind == "language":
        lang, slash, section = name.partition("/")
        lang = lang.strip()
        section = section.strip() if slash else "standards"
        if not lang:
            raise RefError('ref "language" needs a language, e.g. "language:kotlin".')
        if section not in LANGUAGE_SECTIONS:
            raise RefError(
                f"Unknown language section '{section}'. Expected one of: "
                f"{', '.join(LANGUAGE_SECTIONS)}."
            )
        return Ref("language", lang, section)

    return Ref(kind, name)


def _example_name(kind: str) -> str:
    return {
        "language": "kotlin/testing",
        "pattern": "repository",
        "skill": "add-screen",
        "workflow": "bug-fix",
    }.get(kind, "<name>")


def try_parse_ref(raw: str) -> Ref | None:
    """parse_ref, returning None instead of raising. For frontmatter rendering."""
    try:
        return parse_ref(raw)
    except RefError:
        return None


def relative_path(ref: Ref) -> str | None:
    """Standards-corpus path for a ref. None for kinds resolved by lookup."""
    if ref.kind == "agents":
        return "AGENTS.md"
    if ref.kind == "architecture":
        if ref.name:
            return f"architecture/decisions/{ref.name}.md"
        return "architecture/overview.md"
    if ref.kind == "language":
        return f"languages/{ref.name}/{ref.section}.md"
    if ref.kind == "pattern":
        return f"patterns/{ref.name}.md"
    if ref.kind == "skill":
        return f"skills/{ref.name}.md"
    if ref.kind == "workflow":
        return f"workflows/{ref.name}.md"
    if ref.kind == "gate":
        return "gates/README.md"
    # guardrails spans two docs, so it has no single static path.
    return None


def format_ref_call(project: str, ref: Ref) -> str:
    """Render a ref as the literal playbook_get call an agent should make."""
    return f'`playbook_get(project="{project}", ref="{ref}")` - {ref.label}'


__all__ = [
    "LANGUAGE_SECTIONS",
    "REF_KINDS",
    "Ref",
    "RefError",
    "format_ref_call",
    "parse_ref",
    "relative_path",
    "try_parse_ref",
]
