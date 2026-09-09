"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

# Make `mcp/` importable as a flat package layout.
_MCP_DIR = Path(__file__).resolve().parent.parent
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))
