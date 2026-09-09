"""
templates_source.py - Where project templates are read from.

A single indirection so the rest of the codebase never learns whether a
template came from the copy bundled in this repo or from a checkout of the
standalone template repo.

Today only the bundled directory exists. When template syncing lands, a shallow
git clone into the cache directory is all that is needed: the cache root is
searched first, and the bundled set stays as the always-present fallback so the
server still works offline and in a fresh container.

Override the cache location with MCP_TEMPLATE_CACHE.
"""

from __future__ import annotations

import os
from pathlib import Path

BUNDLED_ROOT = Path(__file__).resolve().parent / "templates"

_DEFAULT_CACHE = Path.home() / ".cache" / "dev-playbook-templates"


def cache_root() -> Path:
    """Where a synced copy of the template repo would live."""
    override = os.environ.get("MCP_TEMPLATE_CACHE")
    return Path(override).expanduser() if override else _DEFAULT_CACHE


def template_roots() -> list[Path]:
    """Directories to search, highest precedence first.

    The cache (a synced checkout) wins over the bundled copy so a template can be
    updated without shipping a new server build. Missing roots are skipped.
    """
    roots = [cache_root(), BUNDLED_ROOT]
    return [r for r in roots if r.is_dir()]


def find_manifests() -> list[Path]:
    """Every pack.yaml under the search roots, highest precedence first.

    Globbed at any depth so the current layout (`base/pack.yaml`,
    `languages/java/pack.yaml`) and a future version-split layout
    (`languages/java/21/pack.yaml`) both load without a loader change.
    """
    found: list[Path] = []
    for root in template_roots():
        found.extend(sorted(root.rglob("pack.yaml")))
    return found


__all__ = ["BUNDLED_ROOT", "cache_root", "find_manifests", "template_roots"]
