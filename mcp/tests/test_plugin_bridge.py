"""The stdio<->SSE bridge, against a real server on a real socket.

The bridge is the plugin's one genuinely new moving part: everything else
reuses code that already had tests. Mocking the transports would test the mock,
so this starts the actual SSE server, runs the actual bridge against it, and
asserts a client speaking stdio to the bridge sees what the SSE server serves.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parent.parent
BRIDGE = MCP_DIR.parent / "plugins" / "dev-playbook" / "scripts" / "sse_bridge.py"

EXPECTED_TOOLS = {
    "playbook_start_task",
    "playbook_get_standard",
    "playbook_find_standards",
    "playbook_list_templates",
    "playbook_scaffold_standards",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def sse_server(tmp_path_factory):
    """A real dev-playbook server on a loopback port, seeded with one project."""
    tmp_path = tmp_path_factory.mktemp("bridge-server")
    port = _free_port()
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            [
                {
                    "project": "bridged",
                    "relative_path": "core/guardrails.md",
                    "kind": "markdown",
                    "is_executable": False,
                    "content": "# Guardrails\n\n- Do the right thing.\n",
                }
            ]
        )
    )
    env = dict(os.environ)
    env.update(
        {
            "MCP_DB_PATH": str(tmp_path / "team.db"),
            "MCP_PORT": str(port),
            "MCP_HOST": "127.0.0.1",
            "MCP_STANDARDS_SEED": str(seed),
        }
    )
    env.pop("MCP_CONFIG", None)
    proc = subprocess.Popen(
        [sys.executable, str(MCP_DIR / "server.py")],
        cwd=str(MCP_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"server exited early: {proc.communicate()[1]}")
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=1) as resp:
                if resp.status == 200:
                    break
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server never became healthy")

    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _through_bridge(url: str, requests: list[dict], expect_ids: tuple[int, ...]) -> dict:
    """Speak stdio to the bridge; return the responses it hands back, by id."""
    proc = subprocess.Popen(
        [sys.executable, str(BRIDGE), url],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(MCP_DIR),
    )
    seen: dict[int, dict] = {}
    try:
        assert proc.stdin and proc.stdout
        for request in requests:
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
        while set(expect_ids) - set(seen):
            line = proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            if "id" in msg:
                seen[msg["id"]] = msg
    finally:
        proc.kill()
        proc.communicate(timeout=15)
    return seen


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest-bridge", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
FIND = {
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {"name": "playbook_find_standards", "arguments": {"project": "bridged"}},
}


@pytest.fixture(scope="module")
def bridged(sse_server):
    return _through_bridge(sse_server, [INITIALIZE, INITIALIZED, TOOLS_LIST, FIND], (1, 2, 3))


def test_the_bridge_passes_the_servers_identity_through(bridged):
    import server

    result = bridged[1]["result"]
    assert result["serverInfo"]["name"] == server.SERVER_LABEL
    assert result["serverInfo"]["version"] == server.SERVER_VERSION


def test_the_bridge_advertises_exactly_what_the_server_advertises(bridged):
    """The bridge inspects nothing, so team mode cannot drift from the server."""
    assert {t["name"] for t in bridged[2]["result"]["tools"]} == EXPECTED_TOOLS


def test_a_tool_call_round_trips(bridged):
    result = bridged[3]["result"]
    assert result.get("isError") is not True
    assert "bridged" in result["content"][0]["text"]


def test_telemetry_reaches_the_shared_server(sse_server, bridged):
    """Team mode must not lose the calls the dashboard exists to show."""
    with urllib.request.urlopen(f"{sse_server}/dashboard/tools", timeout=10) as resp:
        body = resp.read().decode("utf-8", "replace")
    assert "playbook_find_standards" in body


def test_a_bad_url_fails_with_the_fix_not_a_traceback():
    proc = subprocess.run(
        [sys.executable, str(BRIDGE), "localhost:3199"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(MCP_DIR),
    )
    assert proc.returncode == 1
    assert "must start with http://" in proc.stderr
    assert "server_url" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_an_unreachable_server_fails_with_the_fix_not_a_traceback():
    port = _free_port()  # nothing is listening here
    proc = subprocess.run(
        [sys.executable, str(BRIDGE), f"http://127.0.0.1:{port}"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(MCP_DIR),
    )
    assert proc.returncode == 1
    assert "Could not reach the shared server" in proc.stderr
    assert "Traceback" not in proc.stderr
