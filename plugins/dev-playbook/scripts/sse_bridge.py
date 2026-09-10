"""sse_bridge.py - speak stdio to Claude Code, SSE to a shared dev-playbook.

Claude Code launches plugin MCP servers over stdio. A team pointing at a shared
server - the one with the dashboard and everyone's telemetry - needs those two
transports joined. That is all this does: pump JSON-RPC messages between the
stdio transport and an SSE client session, in both directions, without
inspecting them.

Deliberately not a session: no capability translation, no request tracking, no
tool list of its own. Whatever the shared server advertises is what Claude Code
sees, so the two transports cannot drift apart.

Usage:  python sse_bridge.py <base-url-or-sse-url>
Env:    PLAYBOOK_TOKEN - bearer token, when the shared server has auth on.

stdout is the protocol stream; every diagnostic goes to stderr.
"""

from __future__ import annotations

import logging
import os
import sys

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.sse import sse_client
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] dev-playbook-bridge: %(message)s",
)
logger = logging.getLogger("dev-playbook-bridge")

# The server mounts SSE at /sse. Accepting a bare base URL means a user can
# paste the address they already use for the dashboard.
_SSE_PATH = "/sse"


def sse_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if not url:
        raise ValueError("empty server URL")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"server URL must start with http:// or https:// - got {raw!r}")
    if url.endswith(_SSE_PATH):
        return url
    return url + _SSE_PATH


async def _pump(
    source: MemoryObjectReceiveStream[SessionMessage | Exception],
    sink: MemoryObjectSendStream[SessionMessage],
    direction: str,
) -> None:
    """Forward one direction until the source closes.

    Both transports put deserialization failures on the read stream as
    Exception values rather than raising. Forwarding is impossible and dying
    would take the whole session down over one bad frame, so they are logged
    and dropped - the peer's own timeout is the honest recovery.
    """
    async with source, sink:
        async for message in source:
            if isinstance(message, Exception):
                logger.warning("%s: dropping unparseable message: %s", direction, message)
                continue
            await sink.send(message)


async def _run(url: str, headers: dict[str, str]) -> None:
    async with sse_client(url, headers=headers or None) as (sse_read, sse_write):
        async with stdio_server() as (stdio_read, stdio_write):
            logger.info("Bridging stdio <-> %s", url)
            async with anyio.create_task_group() as tg:
                tg.start_soon(_pump, stdio_read, sse_write, "client->server")
                tg.start_soon(_pump, sse_read, stdio_write, "server->client")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    raw = args[0] if args else os.environ.get("PLAYBOOK_SERVER_URL", "")
    try:
        url = sse_url(raw)
    except ValueError as exc:
        logger.error(
            "%s.\n  Fix: set the plugin's 'server_url' option to your shared "
            "dev-playbook server, e.g. http://localhost:3001 - or clear it to "
            "run a local server instead.",
            exc,
        )
        return 1

    headers: dict[str, str] = {"X-MCP-Editor": os.environ.get("MCP_EDITOR", "claude-code")}
    token = os.environ.get("PLAYBOOK_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        anyio.run(_run, url, headers)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Could not reach the shared server at %s: %s\n"
            "  Fix: check the server is running and the URL is right "
            "(its /healthz should return 'ok'). If it has auth enabled, set the "
            "plugin's 'token' option to a bearer token from POST /auth/login.",
            url,
            exc,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
