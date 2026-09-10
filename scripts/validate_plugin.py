#!/usr/bin/env python3
"""Check the Claude Code plugin is well-formed, using nothing but the stdlib.

`claude plugin validate` is the real validator, and CI runs it when the CLI is
installable. This is the floor underneath it: it runs everywhere, needs nothing
installed, and never goes stale against a CLI release. It checks the things
whose failure mode is silent - a plugin that simply does not load, with no
error anyone sees.

Usage:  python3 scripts/validate_plugin.py [repo root]
Exit:   0 when everything holds, 1 with one line per problem otherwise.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

PLUGIN_DIR = Path("plugins") / "dev-playbook"
MARKETPLACE = Path(".claude-plugin") / "marketplace.json"

# Only plugin.json belongs in .claude-plugin/. Putting skills/, hooks/ or
# .mcp.json in there is the documented common mistake, and it fails by the
# component simply never loading.
ROOT_ONLY = ("skills", "hooks", ".mcp.json", "scripts", "agents", "commands")

HOOK_SCRIPTS = ("session_context.py", "edit_gate.py", "playbook_db.py")

problems: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        fail(f"missing: {path}")
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        fail(f"{path}: invalid JSON - {exc}")
        return None
    if not isinstance(data, dict):
        fail(f"{path}: top level must be an object")
        return None
    return data


def check_plugin_manifest(root: Path) -> None:
    manifest = load_json(root / PLUGIN_DIR / ".claude-plugin" / "plugin.json")
    if manifest is None:
        return
    for key in ("name", "version", "description"):
        if not manifest.get(key):
            fail(f"plugin.json: missing required key '{key}'")

    name = str(manifest.get("name", ""))
    # Reserved namespaces: a plugin called claude-anything cannot be published.
    if name.startswith(("claude-", "anthropic-")):
        fail(f"plugin.json: name '{name}' uses a reserved prefix (claude-/anthropic-)")

    # Every userConfig entry needs a title; the CLI validator rejects the
    # manifest without one, and that error only appears at install time.
    for key, spec in (manifest.get("userConfig") or {}).items():
        if not isinstance(spec, dict):
            fail(f"plugin.json: userConfig.{key} must be an object")
            continue
        for required in ("type", "title", "description"):
            if not spec.get(required):
                fail(f"plugin.json: userConfig.{key} is missing '{required}'")


def check_marketplace(root: Path) -> None:
    manifest = load_json(root / MARKETPLACE)
    if manifest is None:
        return
    for key in ("name", "owner", "plugins"):
        if not manifest.get(key):
            fail(f"marketplace.json: missing required key '{key}'")

    entries = manifest.get("plugins")
    if not isinstance(entries, list) or not entries:
        fail("marketplace.json: 'plugins' must be a non-empty array")
        return

    plugin_manifest = load_json(root / PLUGIN_DIR / ".claude-plugin" / "plugin.json") or {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            fail(f"marketplace.json: plugins[{i}] must be an object")
            continue
        for key in ("name", "source"):
            if not entry.get(key):
                fail(f"marketplace.json: plugins[{i}] is missing '{key}'")

        source = str(entry.get("source", ""))
        if source.startswith("./") and not (root / source).is_dir():
            fail(f"marketplace.json: plugins[{i}].source '{source}' is not a directory")

        # One repo carries both manifests, so there is one version to bump -
        # which only holds if they actually agree.
        if entry.get("name") == plugin_manifest.get("name"):
            if entry.get("version") != plugin_manifest.get("version"):
                fail(
                    f"marketplace.json: plugins[{i}].version "
                    f"{entry.get('version')!r} != plugin.json version "
                    f"{plugin_manifest.get('version')!r}"
                )


def check_layout(root: Path) -> None:
    plugin_root = root / PLUGIN_DIR
    if not plugin_root.is_dir():
        fail(f"missing plugin root: {PLUGIN_DIR}")
        return
    for name in ROOT_ONLY:
        stray = plugin_root / ".claude-plugin" / name
        if stray.exists():
            fail(f"{PLUGIN_DIR}/.claude-plugin/{name} must live at the plugin root, not inside")

    for required in (".mcp.json", "hooks/hooks.json"):
        if not (plugin_root / required).is_file():
            fail(f"missing: {PLUGIN_DIR}/{required}")

    for path in plugin_root.glob("**/*.json"):
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            fail(f"{path.relative_to(root)}: invalid JSON - {exc}")

    for skill in ("using-standards", "scaffold-standards"):
        md = plugin_root / "skills" / skill / "SKILL.md"
        if not md.is_file():
            fail(f"missing: {PLUGIN_DIR}/skills/{skill}/SKILL.md")
        elif not md.read_text().startswith("---"):
            fail(f"{PLUGIN_DIR}/skills/{skill}/SKILL.md: no YAML frontmatter")

    selector = plugin_root / "scripts" / "playbook-mcp.sh"
    if selector.is_file() and not selector.stat().st_mode & 0o111:
        fail(f"{PLUGIN_DIR}/scripts/playbook-mcp.sh is not executable")


def check_hooks_json(root: Path) -> None:
    """Every hook entry must be in a form the runtime actually accepts.

    A malformed entry is not an error at load time - it is dropped with
    "entry ignored at runtime", so the hook simply never fires and nothing
    says so. Exec form (an `args` array) is one of these: it validates as a
    plugin but the hook is silently discarded. Shell form is what runs.
    """
    path = root / PLUGIN_DIR / "hooks" / "hooks.json"
    if not path.is_file():
        return  # check_layout already reported it
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return  # check_layout already reported it

    events = data.get("hooks")
    if not isinstance(events, dict) or not events:
        fail("hooks.json: 'hooks' must be a non-empty object of event names")
        return

    for event, matchers in events.items():
        if not isinstance(matchers, list):
            fail(f"hooks.json: {event} must be an array")
            continue
        for i, matcher in enumerate(matchers):
            entries = (matcher or {}).get("hooks") if isinstance(matcher, dict) else None
            if not isinstance(entries, list) or not entries:
                fail(f"hooks.json: {event}[{i}] has no 'hooks' array")
                continue
            for j, entry in enumerate(entries):
                where = f"hooks.json: {event}[{i}].hooks[{j}]"
                if not isinstance(entry, dict):
                    fail(f"{where} must be an object")
                    continue
                if entry.get("type") != "command":
                    fail(f"{where} has type {entry.get('type')!r}, expected 'command'")
                if "args" in entry:
                    fail(
                        f"{where} uses exec form ('args'), which the runtime ignores "
                        "silently - use shell form ('command')"
                    )
                command = entry.get("command")
                if not isinstance(command, str) or not command.strip():
                    fail(f"{where} is missing a non-empty 'command' string")
                    continue
                # The script it names has to exist, or the hook fails on every
                # single invocation with nothing but an exit code to show for it.
                for script in HOOK_SCRIPTS:
                    if script in command and not (root / PLUGIN_DIR / "scripts" / script).is_file():
                        fail(f"{where} runs {script}, which does not exist")


def check_hooks_are_stdlib_only(root: Path) -> None:
    """Hooks run on every session start and every edit.

    A third-party import here would mean the hook works only where that package
    happens to be installed - and it fails silently, because a hook that cannot
    start exits non-zero into a log nobody reads.
    """
    scripts_dir = root / PLUGIN_DIR / "scripts"
    local = {p.stem for p in scripts_dir.glob("*.py")}
    for name in HOOK_SCRIPTS:
        path = scripts_dir / name
        if not path.is_file():
            fail(f"missing: {PLUGIN_DIR}/scripts/{name}")
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as exc:
            fail(f"{PLUGIN_DIR}/scripts/{name}: syntax error - {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [(node.module or "").split(".")[0]]
            else:
                continue
            for module in modules:
                if not module or module in local or module == "__future__":
                    continue
                if module not in sys.stdlib_module_names:
                    fail(f"{PLUGIN_DIR}/scripts/{name}: imports non-stdlib module '{module}'")


def main() -> int:
    root = (
        Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    )
    check_plugin_manifest(root)
    check_marketplace(root)
    check_layout(root)
    check_hooks_json(root)
    check_hooks_are_stdlib_only(root)

    if problems:
        print(f"✘ {len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("✔ plugin manifests, layout and hook scripts look right")
    return 0


if __name__ == "__main__":
    sys.exit(main())
