"""Every document tool as two domain tools -- one endpoint, `action` plus `args`.

The read and edit tiers list 13 tools between them; a model connected to both
reads 13 names on every turn. This endpoint lists two -- docs_read and
docs_edit -- and each tool's `action` is one of those 13 tools by its own name. Schemas,
validation, wrappers and answers are the tiers' own: see shared/domain_tools.py.
The tier endpoints keep serving unchanged, for small local models and for
every client already connected to one.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

_root = str(Path(__file__).resolve().parents[2])
if _root not in sys.path:
    sys.path.insert(0, _root)

from mcp.server.fastmcp import FastMCP  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from servers.docs_edit.server import mcp as edit  # noqa: E402
from servers.docs_read.server import mcp as read  # noqa: E402
from shared.arg_errors import contract_errors  # noqa: E402
from shared.deploy_auth import build_auth, build_oauth_bridge  # noqa: E402
from shared.domain_tools import register_domains  # noqa: E402
from shared.strict_args import enforce_known_arguments  # noqa: E402

_VERSION = "0.2.0"  # keep in sync with pyproject.toml [project].version

_oauth_bridge = build_oauth_bridge(
    "DOCS", state_dir=os.environ.get("DOCS_DOMAIN_OAUTH_STATE_DIR", "/tmp/docs-domain-oauth-state")
)
_public_origin = os.environ.get("DOCS_PUBLIC_URL", "").rstrip("/")
_HOST = os.environ.get("DOCS_DOMAIN_HOST", "127.0.0.1")
_PORT = int(os.environ.get("DOCS_DOMAIN_PORT", "8853"))
_token_verifier, _auth_settings = build_auth("DOCS", _public_origin or None, _oauth_bridge)

mcp = FastMCP("docs", host=_HOST, port=_PORT, token_verifier=_token_verifier, auth=_auth_settings)
if _oauth_bridge is not None:
    _oauth_bridge.register_routes(mcp)

# Each domain: what it is for, then its actions -- each an existing tier tool.
DOMAINS = {
    "docs_read": (
        "Read any document without changing it: probe, outline, find, extract text or tables, a page, Markdown.",
        [
            (read, "probe"),
            (read, "outline"),
            (read, "find"),
            (read, "extract"),
            (read, "extract_tables"),
            (read, "read_page"),
            (read, "to_markdown"),
        ],
    ),
    "docs_edit": (
        "Make a new document from others: assemble, convert, optimize, OCR, protect, redact.",
        [
            (edit, "assemble"),
            (edit, "convert"),
            (edit, "optimize"),
            (edit, "ocr"),
            (edit, "protect"),
            (edit, "redact"),
        ],
    ),
}
register_domains(mcp, DOMAINS)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness check. Unauthenticated."""
    return JSONResponse({"status": "ok", "version": _VERSION, "tools": len(DOMAINS)})


# A wrong-typed `args` or an unknown top-level key gets the fleet's failure
# shape, as on every tier; per-action arguments are checked by the dispatcher.
contract_errors(mcp)
enforce_known_arguments(mcp)


def main() -> None:
    parser = argparse.ArgumentParser(description="docs domain MCP Server")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default=os.environ.get("DOCS_DOMAIN_TRANSPORT", "stdio")
    )
    args = parser.parse_args()
    mcp.run(transport="streamable-http" if args.transport == "http" else "stdio")


if __name__ == "__main__":
    main()
