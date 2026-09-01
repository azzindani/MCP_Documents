"""Combined HTTP entry point — both sub-servers in ONE process, ONE port.

Each sub-server keeps its own server.py for stdio use. This file is the
Docker/remote deployment: it imports each already-built FastMCP instance and
mounts its HTTP app at a path prefix inside one Starlette app, so pypdfium2,
pdfplumber and pikepdf load once rather than twice.

Three things here are not obvious and each cost a sibling repo real time:

  lifespans do NOT propagate through Starlette's Mount(), so each sub-server's
  session-manager lifespan is entered explicitly via AsyncExitStack. The
  official SDK returns a plain Starlette app, so the lifespan is reached via
  `app.router.lifespan_context`.

  DNS-rebinding Host validation is ON by default and rejects every real request
  behind a reverse proxy with 421 "Invalid Host header" -- while /health and
  the container healthcheck both pass, which is what makes it hard to find.

  RFC 8414/9728 clients build discovery URLs by INSERTING /.well-known/...
  between the origin and the resource path, landing at the outer app's root,
  while Mount() nests the real routes under each prefix. Hence the redirects.
"""

from __future__ import annotations

import argparse
import os
from contextlib import AsyncExitStack, asynccontextmanager

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route

from servers.docs_edit.server import mcp as edit_mcp
from servers.docs_read.server import mcp as read_mcp

_VERSION = "0.1.0"

_SUB_SERVERS = {
    "read": read_mcp,
    "edit": edit_mcp,
}
# streamable_http_app() takes no path argument -- the mount path comes from
# mcp.settings.streamable_http_path, which already defaults to "/mcp". And
# lifespans do NOT propagate through Starlette's Mount(), so each sub-server's
# session-manager lifespan is entered explicitly below. The official SDK
# returns a plain Starlette app (fastmcp 2.x returned its own subclass with a
# convenience `.lifespan`), so the lifespan is reached via
# `app.router.lifespan_context`. Same pattern as MCP_Microsoft_Office, which
# has been on the official SDK all along.
# Each sub-server's FastMCP defaults to host="127.0.0.1", which auto-enables
# DNS-rebinding Host-header validation restricted to 127.0.0.1/localhost. The
# unified server sits behind Caddy on a public hostname forwarded via
# `header_up Host {host}`, so that check rejects every real remote request with
# a 421 "Invalid Host header" -- healthy container, working /health, and every
# tool call refused. Caddy is already the trust boundary, so disable it for the
# mounted sub-apps. Same fix as MCP_Microsoft_Office, which hit this first.
for _sub_mcp in _SUB_SERVERS.values():
    _sub_mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

_sub_apps = {name: mcp.streamable_http_app() for name, mcp in _SUB_SERVERS.items()}


@asynccontextmanager
async def _combined_lifespan(app):
    async with AsyncExitStack() as stack:
        for sub_app in _sub_apps.values():
            await stack.enter_async_context(sub_app.router.lifespan_context(sub_app))
        yield


async def _root_health(request: Request) -> JSONResponse:
    """Aggregate liveness check. Unauthenticated."""
    return JSONResponse({"status": "ok", "version": _VERSION, "sub_servers": list(_SUB_SERVERS)})


async def _root_version(request: Request) -> JSONResponse:
    """Report running version. Unauthenticated."""
    return JSONResponse({"current": _VERSION})


async def _root(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "server": "MCP_Documents",
            "sub_servers": {name: f"/{name}/mcp" for name in _SUB_SERVERS},
        }
    )


def _redirect(target: str):
    """308 redirect to a sub-server's real well-known route.

    RFC 8414/9728 clients build discovery URLs by inserting
    `/.well-known/...` between the origin and the resource/issuer path
    (e.g. `/.well-known/oauth-protected-resource/basic/mcp`), landing at the
    OUTER app's root. But Mount() nests each sub-server's real well-known
    routes under its own prefix (`/basic/.well-known/...`) instead, so the
    client's computed URL 404s without this redirect — confirmed live
    against a real unauthenticated claude.ai connector attempt.
    """

    async def _handler(request: Request) -> RedirectResponse:
        return RedirectResponse(target, status_code=308)

    return _handler


_discovery_redirects = [
    route
    for name in _SUB_SERVERS
    for route in (
        Route(
            f"/.well-known/oauth-protected-resource/{name}/mcp",
            _redirect(f"/{name}/.well-known/oauth-protected-resource"),
        ),
        Route(
            f"/.well-known/oauth-authorization-server/{name}",
            _redirect(f"/{name}/.well-known/oauth-authorization-server"),
        ),
    )
]

app = Starlette(
    routes=[
        Route("/health", _root_health),
        Route("/version", _root_version),
        Route("/", _root),
        *_discovery_redirects,
        *(Mount(f"/{name}", app=sub_app) for name, sub_app in _sub_apps.items()),
    ],
    lifespan=_combined_lifespan,
)


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="MCP_Documents unified server")
    parser.add_argument("--host", default=os.environ.get("DOCS_HOST", "0.0.0.0"))
    # 8850, and 8850-8859 is this repo's block. The fleet's ports are allocated
    # by hand and 8816 -- the original default here -- was already
    # DATA_WORKSPACE_PORT, so running MCP_Documents and MCP_Data_Analyst as
    # standalone HTTP servers on one box raced for the same socket. Whichever
    # bound second died with EADDRINUSE, which is loud; the worse case is a
    # client configured for 8816 that reaches the other server's tool list.
    # Taken: 8765 math, 8801 fs, 8810-8816 data, 8820-8822 ml, 8830-8840 office.
    parser.add_argument("--port", type=int, default=int(os.environ.get("DOCS_PORT", "8850")))
    args = parser.parse_args()
    # timeout_keep_alive must exceed the reverse proxy's idle-connection pool,
    # or the proxy reuses a connection this server has already closed. uvicorn's
    # default is 5s and Caddy pools for 2 minutes, so every connection idle
    # between the two was dead here and live there. Reusing one gave Caddy
    # "aborting with incomplete response ... use of closed network connection",
    # which it turned into a 200 with zero bytes: the tool call had run, and the
    # caller hung until its own timeout believing it had failed. Measured
    # against the deployment: idle 2s reused fine, idle 7s closed.
    keepalive = int(os.environ.get("MCP_KEEPALIVE_SECONDS", "300"))
    uvicorn.run(app, host=args.host, port=args.port, timeout_keep_alive=keepalive)


if __name__ == "__main__":
    main()
