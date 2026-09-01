"""docs-edit MCP server — thin wrapper only. Zero domain logic.

Every tool body is one line. Docstrings are <= 80 characters and are the entire
contract the model reads: live MCP schemas carry no enums and no parameter
descriptions, so every string argument arrives as a bare {"type": "string"}.

Every tool here WRITES. `protect` and `redact` are additionally marked
destructive, because they produce a file whose whole purpose is that the
original content is no longer in it -- a client that gates destructive tools
behind a confirmation should prompt for those two and not for a page merge.
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

from servers.docs_edit import engine  # noqa: E402
from shared.deploy_auth import build_auth, build_oauth_bridge  # noqa: E402
from shared.json_safe import sanitize_responses  # noqa: E402
from shared.token_estimate import measure_responses  # noqa: E402
from shared.tool_annotations import CREATES, EDITS  # noqa: E402

_VERSION = "0.1.0"  # keep in sync with pyproject.toml [project].version

_oauth_bridge = build_oauth_bridge(
    "DOCS", state_dir=os.environ.get("DOCS_EDIT_OAUTH_STATE_DIR", "/tmp/docs-edit-oauth-state")
)
_public_origin = os.environ.get("DOCS_PUBLIC_URL", "").rstrip("/")
_base_url = f"{_public_origin}/edit" if _public_origin else None
_HOST = os.environ.get("DOCS_EDIT_HOST", "127.0.0.1")
_PORT = int(os.environ.get("DOCS_EDIT_PORT", "8852"))  # 8850 block; see unified_server.py
_token_verifier, _auth_settings = build_auth("DOCS", _base_url, _oauth_bridge)

mcp = FastMCP(
    "docs_edit",
    host=_HOST,
    port=_PORT,
    token_verifier=_token_verifier,
    auth=_auth_settings,
)
if _oauth_bridge is not None:
    _oauth_bridge.register_routes(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness check. Unauthenticated."""
    return JSONResponse({"status": "ok", "version": _VERSION})


@mcp.custom_route("/version", methods=["GET"])
async def version(request: Request) -> JSONResponse:
    """Report running version. Unauthenticated."""
    return JSONResponse({"current": _VERSION})


@mcp.tool(annotations=CREATES)
def assemble(sources: list[str], select: str, out: str) -> dict:
    """Build a document from a page selection: merge, split, reorder, rotate."""
    return engine.assemble(sources, select, out)


@mcp.tool(annotations=CREATES)
def convert(source: str, to: str, out: str = "") -> dict:
    """Convert between formats: pdf, txt, md, html, images."""
    return engine.convert(source, to, out)


@mcp.tool(annotations=CREATES)
def optimize(source: str, action: str = "compress", out: str = "") -> dict:
    """Compress, repair or linearise a PDF. Reports the real size change."""
    return engine.optimize(source, action, out)


@mcp.tool(annotations=CREATES)
def ocr(source: str, pages: str = "", language: str = "eng", out: str = "") -> dict:
    """Add a searchable text layer to scanned pages. Page range required."""
    return engine.ocr(source, pages, language, out)


@mcp.tool(annotations=EDITS)
def protect(source: str, action: str, password: str = "", out: str = "") -> dict:
    """Encrypt, decrypt, or clear a PDF's permission flags. Needs a password."""
    return engine.protect(source, action, password, out)


@mcp.tool(annotations=EDITS)
def redact(source: str, pattern: str, pages: str = "", regex: bool = False, out: str = "") -> dict:
    """Permanently remove matching content, then verify it cannot be extracted."""
    return engine.redact(source, pattern, pages, regex, out)


# Applied to the whole server rather than per tool, deliberately. Both of these
# were fixed once at a call site in a sibling repo and stopped at that one
# site: json_safe existed for a year while every other tool kept emitting bare
# Infinity, which is not JSON and takes out the whole response for any non-
# Python client. A choke point cannot be forgotten by the next tool added.
sanitize_responses(mcp)
measure_responses(mcp)


def main() -> None:
    parser = argparse.ArgumentParser(description="docs-edit MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default=os.environ.get("DOCS_TRANSPORT", "stdio"))
    args = parser.parse_args()

    if args.transport == "http":
        # uvicorn is driven directly rather than through
        # mcp.run("streamable-http") because the SDK builds uvicorn.Config
        # without timeout_keep_alive. uvicorn's default is 5s while a reverse
        # proxy pools upstream connections for 2 minutes, so any connection
        # idle between the two is dead here and live in the pool: the proxy
        # reuses it, logs "aborting with incomplete response ... use of closed
        # network connection", and writes a 200 with ZERO BYTES -- after the
        # tool has already run. The caller cannot tell that from a failure, and
        # a retry double-applies anything that is not idempotent. All six
        # sibling repos carry this fix; do not reintroduce the bug here.
        import uvicorn

        uvicorn.run(
            mcp.streamable_http_app(),
            host=mcp.settings.host,
            port=mcp.settings.port,
            timeout_keep_alive=int(os.environ.get("MCP_KEEPALIVE_SECONDS", "300")),
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
