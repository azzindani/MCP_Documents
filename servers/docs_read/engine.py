"""Thin router. Zero domain logic, zero MCP imports.

server.py imports only from here and every tool body is one line, so the tool
surface reads in one screen and the domain code is testable without an MCP
process. Tests import this module, never the server.

Tools land here as they are built (CLAUDE.md §14). An unimplemented tool is
ABSENT rather than stubbed: a registered tool that always fails still answers
tools/list, still costs context in every model's tool list, and still counts as
covered once the smoke guard has called it once.
"""

from __future__ import annotations

from servers.docs_read._read_extract import extract, extract_tables
from servers.docs_read._read_find import find
from servers.docs_read._read_page import read_page, to_markdown
from servers.docs_read._read_probe import outline, probe

__all__ = ["extract", "extract_tables", "find", "probe", "outline", "read_page", "to_markdown"]
