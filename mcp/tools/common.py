"""Shared helpers for the playbook_* tools.

Annotation constants, argument coercion, project resolution and the Next Calls
renderer. Nothing here knows about SSE or Starlette - a tool module is a pure
function of its arguments plus the StandardsStore.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.types import TextContent, ToolAnnotations

from standards_store import FileRow, StandardsStore

# ---------------------------------------------------------------------------
# Annotations
#
# Declared explicitly on every tool. A client that sees no annotations is
# entitled to assume the worst, so silence would make every read tool look
# destructive.
# ---------------------------------------------------------------------------

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

# Scaffolding adds a project that was not there; it never rewrites one (the
# store refuses to merge). But calling it twice is an error rather than a
# no-op, so it is not idempotent - which is what makes a client confirm.
WRITE_ADDITIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

# ---------------------------------------------------------------------------
# Write policy
#
# Set once by server.build_app(). Lives here rather than being imported from
# server.py because the tool modules are imported *by* server.py - reaching
# back would be circular.
# ---------------------------------------------------------------------------


@dataclass
class WritePolicy:
    """Whether scaffolding is offered, and whether it needs an admin.

    ``auth_enabled`` matters because with auth off every principal is
    role="user" (identity.py), so an unconditional admin check would make the
    tool permanently unusable in the default local config.
    """

    scaffold_enabled: bool = True
    auth_enabled: bool = False


POLICY = WritePolicy()


def set_policy(*, scaffold_enabled: bool, auth_enabled: bool) -> None:
    POLICY.scaffold_enabled = scaffold_enabled
    POLICY.auth_enabled = auth_enabled


PROJECT_PARAM_DESC = (
    "Standards project name, e.g. 'nexre'. This is the project whose standards "
    "you want, not the language. Call playbook_find_standards with no query to "
    "see what a project contains, or playbook_scaffold_standards to create one."
)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def text(body: str) -> list[TextContent]:
    return [TextContent(type="text", text=body)]


def error(body: str) -> list[TextContent]:
    """An error the model should act on, not a protocol failure.

    Returned as ordinary content so the model reads the steering text; the
    caller sets ctx.status = "error" so telemetry still counts it as one.
    """
    return text(body)


# ---------------------------------------------------------------------------
# Argument coercion
#
# Arguments arrive as whatever JSON the client sent. Coerce rather than trust:
# a model that passes "java" where a list was asked for should get standards,
# not a stack trace.
# ---------------------------------------------------------------------------


def as_str(value: object, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def as_str_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def as_str_set(value: object) -> set[str] | None:
    """None means 'the caller said nothing', which is not the same as empty.

    scaffold_service reads None as "use the pack defaults" and an empty set as
    "the caller deselected everything", so the distinction has to survive.
    """
    if value is None:
        return None
    return set(as_str_list(value))


def as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return default


def as_int(value: object, default: int, *, low: int, high: int) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def as_str_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if v is not None}


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------


async def resolve_project(store: StandardsStore, project: str) -> str | None:
    """Return the stored project name, matched case-insensitively, or None."""
    if not project:
        return None
    names = await store.list_projects()
    if project in names:
        return project
    lowered = project.lower()
    for name in names:
        if name.lower() == lowered:
            return name
    return None


async def unknown_project(store: StandardsStore, project: str) -> str:
    """Steering text for a project that is not in the store.

    Names what exists and what to do about it, rather than saying 'not found'
    and leaving the model to guess its next move.
    """
    names = await store.list_projects()
    if names:
        available = ", ".join(names)
        return (
            f"No standards project named '{project}'.\n"
            f"Projects in this instance: {available}\n\n"
            "If one of those is the project you meant, call again with that name. "
            "If this codebase has no standards yet, create them with "
            "playbook_scaffold_standards - call playbook_list_templates first to "
            "see the available language packs."
        )
    return (
        f"No standards project named '{project}'. This instance has no standards "
        "projects at all yet.\n\n"
        "Bootstrap one for this codebase: call playbook_list_templates to see the "
        "language packs, then playbook_scaffold_standards with dry_run=true to "
        "preview what it would generate."
    )


# ---------------------------------------------------------------------------
# Document rendering
#
# render_ref is the single place a document body is formatted. Every getter
# composes it rather than formatting its own, so the same document reads
# identically whichever tool returned it.
# ---------------------------------------------------------------------------


def render_ref(row: FileRow) -> str:
    """The canonical body for one document."""
    header = f"# {row.title}"
    if row.description:
        header += f"\n\n{row.description}"
    location = f"`{row.relative_path}` in project `{row.project}`"
    if row.kind == "script":
        return "\n".join([header, "", location, "", "```sh", row.body.rstrip(), "```"])
    return "\n".join([header, "", location, "", "---", "", row.body.strip()])


# ---------------------------------------------------------------------------
# Path -> tool-call routing / Next Calls
#
# Each document family is served by its own tool, so a "read this next" hint
# has to name the right tool for the path. route_call maps a stored
# relative_path onto the literal call that returns it, and next_calls renders a
# block of them the model can follow verbatim.
# ---------------------------------------------------------------------------

_AGENTS_DOCS = {"AGENTS.md", "ARCHITECTURE.md", "glossary.md", "INDEX.md", "README.md"}
_GUARDRAILS_DOCS = {"guardrails.md", "git.md"}


def _call(project: str, tool: str, **kwargs: str) -> str:
    args = ", ".join([f'project="{project}"', *(f'{k}="{v}"' for k, v in kwargs.items())])
    return f"playbook_{tool}({args})"


def route_call(project: str, relative_path: str) -> str:
    """The literal tool call that returns the document at `relative_path`."""
    path = relative_path
    if path in _AGENTS_DOCS:
        return _call(project, "get_agents")
    if path in _GUARDRAILS_DOCS:
        return _call(project, "get_guardrails")
    if path.startswith("languages/"):
        parts = path.split("/")
        if len(parts) > 2:
            return _call(project, "get_standards", language=parts[1])
        return _call(project, "get_standards")
    if path.startswith("patterns/"):
        name = path.rsplit("/", 1)[-1].removesuffix(".md")
        return _call(project, "get_patterns", name=name)
    if path.startswith("workflows/"):
        name = path.rsplit("/", 1)[-1].removesuffix(".md")
        return _call(project, "get_workflow", name=name)
    if path.startswith("gates/"):
        return _call(project, "get_gates")
    return _call(project, "find_standards")


def next_calls(project: str, entries: list[tuple[str, str]]) -> str:
    """Render a Next Calls block from (relative_path, label) pairs."""
    if not entries:
        return ""
    lines = ["", "## Next Calls", ""]
    lines += [f"- {label}: `{route_call(project, path)}`" for path, label in entries]
    return "\n".join(lines)


__all__ = [
    "POLICY",
    "PROJECT_PARAM_DESC",
    "READ_ONLY",
    "WRITE_ADDITIVE",
    "as_bool",
    "as_int",
    "as_str",
    "as_str_list",
    "as_str_map",
    "as_str_set",
    "error",
    "next_calls",
    "render_ref",
    "route_call",
    "WritePolicy",
    "resolve_project",
    "set_policy",
    "text",
    "unknown_project",
]
