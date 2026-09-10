"""The playbook_* MCP tool surface.

Every module here exports a ``DEFINITIONS`` list and an async ``dispatch``.
``server.py`` concatenates the definitions for ``tools/list`` and walks the
modules on ``tools/call``, taking the first that returns a non-None result.
"""
