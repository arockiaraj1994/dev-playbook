"""Tests for the /dev-playbook-init config helper (plugins/.../scripts/dp_init.py).

Run as a subprocess under the host python3, the way the command invokes it. The
user-scope MCP registration (which shells out to `claude`) is exercised only via
--no-user-scope here, so the tests need no CLI installed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DP_INIT = (
    Path(__file__).resolve().parents[2] / "plugins" / "dev-playbook" / "scripts" / "dp_init.py"
)


def _run(args: list[str], home: Path, data: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home), "CLAUDE_PLUGIN_DATA": str(data)}
    env.pop("PLAYBOOK_DB_PATH", None)
    return subprocess.run(
        [sys.executable, str(DP_INIT), *args],
        input="",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_configured_marker_is_written(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    r = _run(["--configured", "billing-api"], home, data)
    assert r.returncode == 0, r.stderr
    assert (data / "configured" / "billing-api").is_file()


def test_configure_creates_claude_md_and_enforce_marker(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    r = _run(["--no-user-scope"], home, data)
    assert r.returncode == 0, r.stderr
    claude_md = home / ".claude" / "CLAUDE.md"
    assert claude_md.is_file()
    text = claude_md.read_text()
    assert "<!-- dev-playbook:start -->" in text
    assert "/dev-playbook-init" in text
    assert (data / "enforce").is_file()


def test_configure_preserves_existing_claude_md_and_is_idempotent(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    claude_md = home / ".claude" / "CLAUDE.md"
    claude_md.write_text("# My global rules\n\nKeep this.\n")
    data = tmp_path / "data"

    _run(["--no-user-scope"], home, data)
    first = claude_md.read_text()
    assert "Keep this." in first
    assert first.count("<!-- dev-playbook:start -->") == 1

    _run(["--no-user-scope"], home, data)
    second = claude_md.read_text()
    assert second.count("<!-- dev-playbook:start -->") == 1
    assert "Keep this." in second
