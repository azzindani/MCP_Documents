"""Thin router. Zero domain logic, zero MCP imports.

Tests import this module, never the server.
"""

from __future__ import annotations

from servers.docs_edit._edit_assemble import assemble
from servers.docs_edit._edit_convert import convert
from servers.docs_edit._edit_optimize import ocr, optimize
from servers.docs_edit._edit_secure import protect, redact

__all__ = ["assemble", "convert", "ocr", "optimize", "protect", "redact"]
