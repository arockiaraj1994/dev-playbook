"""tools/ - MCP tool modules.

Three tools: playbook_start (entry point), playbook_get (fetch one doc by
`ref`), playbook_find (discover docs). Every one only reads markdown from disk -
nothing writes, mutates, or reaches the network (gate scripts are shown, never
executed) - so a single annotation set applies to all of them and
server.list_tools() stamps it on centrally.

Shared helpers live in tools/common.py; the `ref` doc-address grammar lives in
refs.py and is also used by scripts/validate-rules.py.
"""

from .common import PROJECT_PARAM_DESC, READ_ONLY

__all__ = ["PROJECT_PARAM_DESC", "READ_ONLY"]
