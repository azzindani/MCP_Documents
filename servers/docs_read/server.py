"""docs-read MCP server — thin wrapper only. Zero domain logic.

Every tool body is one line. Docstrings are <= 80 characters and are the entire
contract the model reads: live MCP schemas carry no enums and no parameter
descriptions, so every string argument arrives as a bare {"type": "string"}.

Because the docstrings are the only contract, they teach the ORDER as well as
the tool: probe before find, find before extract. A caller that runs extract on
a 500-page scan gets an empty string and no idea why; one that probed first was
told which pages have no text layer, in a syntax it can paste into ocr().
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

from servers.docs_read import engine  # noqa: E402
from shared.deploy_auth import build_auth, build_oauth_bridge  # noqa: E402
from shared.json_safe import sanitize_responses  # noqa: E402
from shared.token_estimate import measure_responses  # noqa: E402
from shared.tool_annotations import READS  # noqa: E402

_VERSION = "0.0.1"  # keep in sync with pyproject.toml [project].version

_oauth_bridge = build_oauth_bridge(
    "DOCS", state_dir=os.environ.get("DOCS_READ_OAUTH_STATE_DIR", "/tmp/docs-read-oauth-state")
)
_public_origin = os.environ.get("DOCS_PUBLIC_URL", "").rstrip("/")
_base_url = f"{_public_origin}/read" if _public_origin else None
_HOST = os.environ.get("DOCS_READ_HOST", "127.0.0.1")
_PORT = int(os.environ.get("DOCS_READ_PORT", "8851"))  # 8850 block; see unified_server.py
_token_verifier, _auth_settings = build_auth("DOCS", _base_url, _oauth_bridge)

mcp = FastMCP(
    "docs_read",
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


@mcp.tool(annotations=READS)
def probe(source: str, password: str = "") -> dict:
    """Identify a document: format, pages, scanned or digital, what it holds."""
    return engine.probe(source, password)


@mcp.tool(annotations=READS)
def outline(source: str, password: str = "") -> dict:
    """List headings and bookmarks with page anchors. Use before extract."""
    return engine.outline(source, password)


@mcp.tool(annotations=READS)
def find(
    source: str,
    query: str,
    regex: bool = False,
    pages: str = "",
    max_hits: int = 50,
    password: str = "",
) -> dict:
    """Locate text across a document. Returns page locations, not content."""
    return engine.find(source, query, regex, pages, max_hits, password)


@mcp.tool(annotations=READS)
def extract(source: str, pages: str = "", clean_text: bool = True, password: str = "") -> dict:
    """Extract clean text for a page range. Bounded; refuses when too big."""
    return engine.extract(source, pages, clean_text, password)


@mcp.tool(annotations=READS)
def extract_tables(source: str, pages: str = "", min_confidence: float = 0.0, password: str = "") -> dict:
    """Extract tables as rows. Says whether ruling lines or gaps were used."""
    return engine.extract_tables(source, pages, min_confidence, password)


@mcp.tool(annotations=READS)
def read_page(source: str, page: int, password: str = "") -> dict:
    """Read one page: text, tables, links, and how each was obtained."""
    return engine.read_page(source, page, password)


@mcp.tool(annotations=READS)
def to_markdown(source: str, pages: str = "", password: str = "") -> dict:
    """Convert a document to markdown. Refuses when over the token budget."""
    return engine.to_markdown(source, pages, password)


# Applied to the whole server rather than per tool, deliberately. Both of these
# were fixed once at a call site in a sibling repo and stopped at that one
# site: json_safe existed for a year while every other tool kept emitting bare
# Infinity, which is not JSON and takes out the whole response for any non-
# Python client. A choke point cannot be forgotten by the next tool added.
sanitize_responses(mcp)
measure_responses(mcp)


def main() -> None:
    parser = argparse.ArgumentParser(description="docs-read MCP server")
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
