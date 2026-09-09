"""The `ref` grammar: how a tool call addresses one standards document.

A ref *is* the row's `relative_path` in standards_files. Everything else here
is a thin alias layer so an agent can write `guardrails` instead of
`core/guardrails.md`, and so the `workflow:` / `gate:` prefixes that read
naturally in prose resolve to real paths.

This deliberately replaces the v0.8.0 grammar (`pattern:x`,
`language:kotlin/testing`), which addressed a filesystem corpus that no longer
exists. Resolution here is always checked against actual store rows - an alias
that no project happens to contain is a miss, not a guess.
"""

from __future__ import annotations

# Bare aliases for the singleton documents every scaffolded project has.
_CORE_ALIASES = {
    "guardrails": "core/guardrails.md",
    "git": "core/git.md",
    "definition-of-done": "core/definition-of-done.md",
    "dod": "core/definition-of-done.md",
    "glossary": "core/glossary.md",
    "anti-patterns": "core/anti-patterns.md",
}

_ROOT_ALIASES = {
    "agents": "AGENTS.md",
    "architecture": "ARCHITECTURE.md",
    "readme": "README.md",
    "index": "INDEX.md",
}

# `kind:name` prefixes that map onto a directory.
_PREFIX_DIRS = {
    "workflow": ("workflows/", ".md"),
    "workflows": ("workflows/", ".md"),
    "core": ("core/", ".md"),
    "gate": ("gates/", ""),
    "gates": ("gates/", ""),
}


def candidate_paths(ref: str) -> list[str]:
    """Every relative_path a ref might mean, best guess first.

    Returns candidates rather than one path because a ref is resolved against
    the rows a project actually has: `gate:verify` could be `gates/verify` or
    `gates/verify.sh` and only the store knows which.
    """
    raw = (ref or "").strip().strip("/")
    if not raw:
        return []

    out: list[str] = []

    def add(path: str) -> None:
        if path and path not in out:
            out.append(path)

    # An exact path always wins - a caller echoing back a path we printed must
    # not be second-guessed.
    add(raw)

    if ":" in raw:
        prefix, _, rest = raw.partition(":")
        rest = rest.strip().strip("/")
        dirname, suffix = _PREFIX_DIRS.get(prefix.strip().lower(), ("", ""))
        if dirname and rest:
            add(f"{dirname}{rest}")
            if suffix and not rest.endswith(suffix):
                add(f"{dirname}{rest}{suffix}")
        return out

    lowered = raw.lower()
    if lowered in _CORE_ALIASES:
        add(_CORE_ALIASES[lowered])
    if lowered in _ROOT_ALIASES:
        add(_ROOT_ALIASES[lowered])

    # A bare name with no alias and no extension: try the places docs live.
    if "/" not in raw and not raw.endswith(".md"):
        add(f"{raw}.md")
        add(f"core/{raw}.md")
        add(f"workflows/{raw}.md")
        add(f"gates/{raw}")
    elif not raw.endswith(".md") and not raw.startswith("gates/"):
        add(f"{raw}.md")

    return out


def format_ref(relative_path: str) -> str:
    """The shortest ref that resolves back to this path.

    Used when rendering Next Calls: a call the model can copy verbatim reads
    better as `ref="guardrails"` than `ref="core/guardrails.md"`, and both
    resolve to the same row.
    """
    for alias, path in _CORE_ALIASES.items():
        if path == relative_path and alias != "dod":
            return alias
    for alias, path in _ROOT_ALIASES.items():
        if path == relative_path:
            return alias
    if relative_path.startswith("workflows/") and relative_path.endswith(".md"):
        return f"workflow:{relative_path[len('workflows/') : -len('.md')]}"
    if relative_path.startswith("gates/"):
        return f"gate:{relative_path[len('gates/') :]}"
    return relative_path


def format_call(project: str, relative_path: str) -> str:
    return f'playbook_get_standard(project="{project}", ref="{format_ref(relative_path)}")'


__all__ = ["candidate_paths", "format_call", "format_ref"]
