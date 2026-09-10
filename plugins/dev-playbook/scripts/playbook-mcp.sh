#!/usr/bin/env bash
#
# Transport selector for the dev-playbook MCP server.
#
# Claude Code launches this as a stdio MCP server. What it execs depends on
# whether the user configured a shared team server:
#
#   PLAYBOOK_SERVER_URL set    -> sse_bridge.py, speaking stdio to Claude Code
#                                 and SSE to the shared server (which has the
#                                 dashboard and the team's telemetry).
#   otherwise                  -> server.py --stdio, a local server on the
#                                 plugin's own SQLite DB. No infrastructure.
#
# stdout is the MCP protocol stream. Every diagnostic here goes to stderr, and
# every failure is loud and names the fix - a silent exit shows up in Claude
# Code only as a server that never connected.

set -euo pipefail

die() {
  echo "dev-playbook: $*" >&2
  exit 1
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
plugin_root="${PLAYBOOK_PLUGIN_ROOT:-$(dirname -- "$script_dir")}"

# --- Where is the server? ----------------------------------------------------
# The marketplace clone keeps the whole repo, so mcp/ sits two levels up from
# the plugin root. server_path overrides that for a checkout laid out
# differently, or a plugin copied out of its repo.
if [[ -n "${PLAYBOOK_SERVER_PATH:-}" ]]; then
  server_dir="$PLAYBOOK_SERVER_PATH"
  server_dir_source="the plugin's server_path setting"
else
  server_dir="$plugin_root/../../mcp"
  server_dir_source="the default location next to the plugin"
fi

if [[ ! -f "$server_dir/server.py" ]]; then
  die "no server.py under '$server_dir' (from $server_dir_source).
  The plugin needs the dev-playbook server sources to run.
  Fix: set the plugin's 'server_path' option to the repo's mcp/ directory -
  /plugin -> dev-playbook -> Configure, or add
    \"server_path\": \"/path/to/dev-agent-playbook/mcp\"
  to the plugin's config. Clone from
  https://github.com/arockiaraj1994/dev-agent-playbook if you do not have it."
fi
server_dir="$(cd -- "$server_dir" && pwd)"

command -v uv >/dev/null 2>&1 || die "'uv' is not on PATH.
  The server runs under uv, which manages its Python and dependencies.
  Fix: install it from https://docs.astral.sh/uv/getting-started/installation/
  then restart Claude Code."

# --- Team mode: bridge to the shared server ----------------------------------
if [[ -n "${PLAYBOOK_SERVER_URL:-}" ]]; then
  exec uv run --directory "$server_dir" --quiet \
    python "$script_dir/sse_bridge.py" "$PLAYBOOK_SERVER_URL"
fi

# --- Local mode: run the server ourselves ------------------------------------
exec uv run --directory "$server_dir" --quiet server.py --stdio
