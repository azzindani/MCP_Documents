"""Thin router. Zero domain logic, zero MCP imports.

server.py imports only from here and every tool body is one line, so the tool
surface can be read in one screen and the domain code is testable without an
MCP process. Tests import this module, never the server.

Tools land here as they are built (CLAUDE.md §14). Unimplemented tools are
absent rather than stubbed: a registered tool that always fails is worse than
one that is not there, because it answers `tools/list`, it consumes context in
every model's tool list, and the smoke-coverage guard counts it as covered
once it has been called at all.
"""

from __future__ import annotations

from servers.docs_read._read_probe import probe

__all__ = ["probe"]
