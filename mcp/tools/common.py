"""tools/common.py - helpers shared by the three playbook tools.

Project resolution, doc metadata accessors, and the `## Next Calls` renderer.
Kept in one place so playbook_start, playbook_get and playbook_find cannot
drift apart in how they name a project or render a follow-up call.
"""

from __future__ import annotations

from mcp.types import TextContent, ToolAnnotations

from loader import SEE_ALSO_TOOLS, RuleDoc, RulesStore
from refs import Ref, format_ref_call, try_parse_ref

# Every tool this server exposes only reads markdown from disk: nothing writes,
# mutates, or reaches the network (gate scripts are shown, never executed). So a
# single annotation set applies to all of them, and server.list_tools() stamps it
# on centrally rather than each module repeating it.
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

# One shared description for the `project` parameter - every tool requires it,
# so agents see identical wording everywhere.
PROJECT_PARAM_DESC = (
    "Basename of the user's current workspace directory "
    "(e.g. cwd .../NexRe → 'nexre'; case-insensitive match to a standards/ "
    "folder). Do not substitute a different project."
)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


def text(body: str) -> list[TextContent]:
    return [TextContent(type="text", text=body)]


def fail(ctx: object, status: str, message: str) -> list[TextContent]:
    """Set an error status on the call context and return the message."""
    ctx.status = status
    return text(message)


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------


class ProjectResolution:
    """Result of resolving a project argument."""

    __slots__ = ("project", "error_text", "status")

    def __init__(
        self,
        project: str | None = None,
        *,
        error_text: str | None = None,
        status: str = "ok",
    ) -> None:
        self.project = project
        self.error_text = error_text
        self.status = status

    @property
    def ok(self) -> bool:
        return self.error_text is None


def canonical_project(given: str, known: list[str]) -> str | None:
    """Return the project matching `given` (exact, then case-insensitive)."""
    if given in known:
        return given
    return {p.lower(): p for p in known}.get(given.lower())


def resolve_project(store: RulesStore, project_arg: str | None) -> ProjectResolution:
    """Validate `project`, or explain what the valid values are.

    `project` is required on every tool, so an omitted value is an error rather
    than something to infer - except when exactly one project exists, where
    there is nothing to disambiguate.
    """
    given = (project_arg or "").strip()
    known = store.projects()

    if given:
        canonical = canonical_project(given, known)
        if canonical is not None:
            return ProjectResolution(canonical)
        listing = "\n".join(f"- {p}" for p in known) or "(none)"
        return ProjectResolution(
            error_text=(
                f"Project '{given}' not found. "
                "`project` must be the basename of your current workspace "
                "directory (matched to a standards/ folder). Available:\n"
                f"{listing}\n"
                "Retry with one of the projects listed above."
            ),
            status="not_found",
        )

    if len(known) == 1:
        return ProjectResolution(known[0])

    if not known:
        return ProjectResolution(
            error_text="No projects found in the standards repository.",
            status="not_found",
        )

    listing = "\n".join(f"- {p}" for p in known)
    return ProjectResolution(
        error_text=(
            "Which project? Pass `project=` as the basename of your current "
            'workspace directory (e.g. folder NexRe → project="nexre"). '
            f"Available:\n{listing}"
        ),
        status="needs_project",
    )


# ---------------------------------------------------------------------------
# Doc metadata
# ---------------------------------------------------------------------------


def meta_str(doc: RuleDoc, key: str, default: str = "") -> str:
    v = doc.metadata.get(key)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return default


def title(doc: RuleDoc) -> str:
    t = meta_str(doc, "title")
    if t:
        return t
    for line in doc.content.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
        if s and not s.startswith("---"):
            break
    return doc.name


def summary(doc: RuleDoc, max_len: int = 200) -> str:
    desc = meta_str(doc, "description")
    if desc:
        return desc[:max_len]
    for line in doc.content.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("---") or s.startswith(">"):
            continue
        return s[:max_len]
    return ""


def triggers(doc: RuleDoc) -> list[str]:
    raw = doc.metadata.get("triggers") or []
    if not isinstance(raw, list):
        return []
    return [t.strip() for t in raw if isinstance(t, str) and t.strip()]


def get_doc(store: RulesStore, project: str, relative_path: str):
    return store.get(project, relative_path)


# ---------------------------------------------------------------------------
# Next Calls
# ---------------------------------------------------------------------------


def format_tool_call(project: str, name: str) -> str | None:
    """Render a `see_also: [tool:<name>]` entry as a literal tool call."""
    if name not in SEE_ALSO_TOOLS:
        return None
    if name == "playbook_start":
        return (
            f'**START HERE** - `playbook_start(project="{project}", '
            'intent="<one sentence describing what the user asked for>")` '
            "- guardrails + matched workflow in one call"
        )
    if name == "playbook_get":
        return (
            f'`playbook_get(project="{project}", ref="guardrails")` '
            "- fetch any doc by ref (guardrails, pattern:…, workflow:…, req:…)"
        )
    if name == "playbook_find":
        return f'`playbook_find(project="{project}")` - list every doc (add `query=` to search)'
    return None


def next_calls_lines(doc: RuleDoc, project: str, key: str = "see_also") -> list[str]:
    """Rendered `- ...` call bullets from a frontmatter list field.

    Default key is `see_also` (standards docs). Stories use `targets:` - same
    vocabulary, same renderer. Returns bullets only (no header), so callers can
    merge several sources into one section.

    Entries are `<kind>:<name>` refs, plus `tool:<name>` for entry points.
    Unrenderable entries are dropped; scripts/validate-rules.py rejects them at
    commit time so they never reach here silently in a healthy corpus.
    """
    raw = doc.metadata.get(key) or []
    if not isinstance(raw, list) or not raw:
        return []

    lines: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            continue
        kind, _, name = entry.partition(":")
        if kind.strip() == "tool":
            call = format_tool_call(project, name.strip())
        else:
            ref = try_parse_ref(entry)
            call = format_ref_call(project, ref) if ref else None
        if call:
            lines.append(f"- {call}")
    return lines


def render_next_calls(lines: list[str]) -> str:
    """Wrap already-rendered call bullets in a `## Next Calls` section."""
    if not lines:
        return ""
    return "\n\n---\n\n## Next Calls\n\n" + "\n".join(lines) + "\n"


def next_calls_section(doc: RuleDoc, project: str, key: str = "see_also") -> str:
    return render_next_calls(next_calls_lines(doc, project, key))


def ref_call(project: str, ref: Ref) -> str:
    return format_ref_call(project, ref)


__all__ = [
    "PROJECT_PARAM_DESC",
    "READ_ONLY",
    "ProjectResolution",
    "canonical_project",
    "fail",
    "format_tool_call",
    "get_doc",
    "meta_str",
    "next_calls_lines",
    "next_calls_section",
    "ref_call",
    "render_next_calls",
    "resolve_project",
    "summary",
    "text",
    "title",
    "triggers",
]
