"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `mcp/` importable as a flat package layout.
_MCP_DIR = Path(__file__).resolve().parent.parent
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))


@pytest.fixture(autouse=True)
def _bundled_templates_only(tmp_path_factory, monkeypatch):
    """Keep a private template overlay out of the test run.

    The template cache is searched before the bundled packs, so a checkout in
    ~/.cache/dev-playbook-templates would otherwise shadow them and the pack
    tests would pass or fail on this machine differently than in CI. Pointing
    the cache at a path that never exists leaves the bundled set as the only
    root. Subprocess tests inherit it through os.environ.
    """
    missing = tmp_path_factory.getbasetemp() / "no-template-cache"
    monkeypatch.setenv("MCP_TEMPLATE_CACHE", str(missing))
