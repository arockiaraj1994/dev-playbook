"""The --stdio transport, driven as a real subprocess.

The point of these tests is that the plugin's transport advertises exactly what
the SSE transport advertises. Importing _serve_stdio and asserting on it would
prove nothing about the wire, so the server is spawned and spoken to over a
pipe the way Claude Code speaks to it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import server

MCP_DIR = Path(__file__).resolve().parent.parent

EXPECTED_TOOLS = {
    "playbook_start_task",
    "playbook_get_standard",
    "playbook_find_standards",
    "playbook_list_templates",
    "playbook_scaffold_standards",
}


def _frame(payload: dict) -> str:
    return json.dumps(payload) + "\n"


def _drive_stdio(
    tmp_path: Path,
    requests: list[dict],
    *,
    expect_ids: tuple[int, ...] = (),
    env_extra: dict | None = None,
) -> tuple[list[str], str]:
    """Run `server.py --stdio`, send requests, return (stdout lines, stderr).

    stdin stays open until every expected response has been read. Closing it
    first ends the session, and the server can exit before the last response
    reaches the pipe - which is exactly the shape of a real client hanging up.
    """
    env = dict(os.environ)
    env["MCP_DB_PATH"] = str(tmp_path / "playbook.db")
    # Keep the run hermetic: no seed corpus, so the DB is this test's alone.
    env["MCP_STANDARDS_SEED"] = str(tmp_path / "no-such-seed.json")
    env.pop("MCP_CONFIG", None)
    env.update(env_extra or {})

    proc = subprocess.Popen(
        [sys.executable, str(MCP_DIR / "server.py"), "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(MCP_DIR),
        env=env,
    )
    lines: list[str] = []
    try:
        assert proc.stdin and proc.stdout
        for request in requests:
            proc.stdin.write(_frame(request))
            proc.stdin.flush()

        seen: set[int] = set()
        wanted = set(expect_ids)
        while wanted - seen:
            line = proc.stdout.readline()
            if not line:  # the server died; let the assertions report on stderr
                break
            if not line.strip():
                continue
            lines.append(line.rstrip("\n"))
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # test_stdout_carries_nothing_but_protocol reports this
            if isinstance(msg, dict) and "id" in msg:
                seen.add(msg["id"])
    finally:
        # communicate() closes stdin for us, which is what ends the session;
        # closing it here first would make its own flush raise instead.
        try:
            stderr = proc.communicate(timeout=30)[1] or ""
        except subprocess.TimeoutExpired:
            proc.kill()
            stderr = proc.communicate()[1] or ""
    return lines, stderr


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


@pytest.fixture(scope="module")
def handshake(tmp_path_factory):
    """One subprocess run shared by the assertions below - it costs a second."""
    tmp_path = tmp_path_factory.mktemp("stdio")
    return _drive_stdio(tmp_path, [INITIALIZE, INITIALIZED, TOOLS_LIST], expect_ids=(1, 2))


def _responses(lines: list[str]) -> dict:
    out = {}
    for line in lines:
        msg = json.loads(line)
        if "id" in msg:
            out[msg["id"]] = msg
    return out


def test_initialize_returns_the_shared_server_identity(handshake):
    lines, _ = handshake
    result = _responses(lines)[1]["result"]
    assert result["serverInfo"]["name"] == server.SERVER_LABEL
    assert result["serverInfo"]["version"] == server.SERVER_VERSION


def test_initialize_carries_the_same_instructions_as_sse(handshake):
    """Both transports build from _initialization_options(), so this holds by
    construction - the test is here to catch a future fork of that."""
    lines, _ = handshake
    assert _responses(lines)[1]["result"]["instructions"] == server.SERVER_INSTRUCTIONS


def test_tools_list_matches_the_sse_surface(handshake):
    lines, _ = handshake
    tools = _responses(lines)[2]["result"]["tools"]
    assert {t["name"] for t in tools} == EXPECTED_TOOLS


def test_the_entry_point_is_listed_first(handshake):
    lines, _ = handshake
    tools = _responses(lines)[2]["result"]["tools"]
    assert tools[0]["name"] == "playbook_start_task"


def test_every_tool_is_annotated(handshake):
    lines, _ = handshake
    for tool in _responses(lines)[2]["result"]["tools"]:
        assert tool.get("annotations"), f"{tool['name']} went out unannotated"


def test_stdout_carries_nothing_but_protocol(handshake):
    """One stray print corrupts the stream silently, so assert it never happens.

    Logging is configured onto stderr at import time; this is what would catch a
    later `print()` for debugging that got committed.
    """
    lines, stderr = handshake
    for line in lines:
        json.loads(line)  # raises if anything non-JSON reached stdout
    # And prove the logging really did go somewhere - to stderr, not stdout.
    assert "stdio" in stderr.lower()


async def test_stdio_advertises_what_list_tools_advertises(handshake):
    """The in-process surface and the subprocess surface are the same set."""
    lines, _ = handshake
    over_the_wire = {t["name"] for t in _responses(lines)[2]["result"]["tools"]}
    in_process = {t.name for t in await server.list_tools()}
    assert over_the_wire == in_process


def test_scaffold_switch_hides_the_write_tool(tmp_path):
    """[enable] scaffold = false applies on stdio too, not just over SSE."""
    config = tmp_path / "config.toml"
    config.write_text("[enable]\nscaffold = false\n")
    lines, _ = _drive_stdio(
        tmp_path,
        [INITIALIZE, INITIALIZED, TOOLS_LIST],
        expect_ids=(1, 2),
        env_extra={"MCP_CONFIG": str(config)},
    )
    names = {t["name"] for t in _responses(lines)[2]["result"]["tools"]}
    assert names == EXPECTED_TOOLS - {"playbook_scaffold_standards"}


def test_stdio_creates_its_database(tmp_path):
    """The plugin points MCP_DB_PATH at a directory that may not exist yet."""
    db = tmp_path / "nested" / "dir" / "playbook.db"
    _drive_stdio(
        tmp_path,
        [INITIALIZE, INITIALIZED],
        expect_ids=(1,),
        env_extra={"MCP_DB_PATH": str(db)},
    )
    assert db.is_file()
