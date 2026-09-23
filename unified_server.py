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

from servers.docs_domain.server import _oauth_bridge as _domain_bridge
from servers.docs_domain.server import mcp as domain_mcp
from servers.docs_edit.server import mcp as edit_mcp
from servers.docs_read.server import mcp as read_mcp
from shared.exchange import upload_route

_VERSION = "0.2.0"

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
for _sub_mcp in (*_SUB_SERVERS.values(), domain_mcp):
    _sub_mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

_sub_apps = {name: mcp.streamable_http_app() for name, mcp in _SUB_SERVERS.items()}
# The two domain tools, served at the root: /mcp. Both tiers keep their own
# endpoints; see servers/docs_domain/server.py.
_domain_app = domain_mcp.streamable_http_app()


@asynccontextmanager
async def _combined_lifespan(app):
    async with AsyncExitStack() as stack:
        for sub_app in (*_sub_apps.values(), _domain_app):
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
            "mcp": "/mcp",
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


# Both protected-resource paths lead to the tier's metadata. A client derives
# .../oauth-protected-resource/<tier>/mcp from the URL it connects to (RFC
# 9728), and the tier's own 401 names .../oauth-protected-resource/<tier> --
# the SDK builds that from the tier's resource URL, which has no /mcp. Only
# the first was redirected, so the URL the 401 hands a client was a 404.
_discovery_redirects = [
    route
    for name in _SUB_SERVERS
    for route in (
        Route(
            f"/.well-known/oauth-protected-resource/{name}/mcp",
            _redirect(f"/{name}/.well-known/oauth-protected-resource"),
        ),
        Route(
            f"/.well-known/oauth-protected-resource/{name}",
            _redirect(f"/{name}/.well-known/oauth-protected-resource"),
        ),
        Route(
            f"/.well-known/oauth-authorization-server/{name}",
            _redirect(f"/{name}/.well-known/oauth-authorization-server"),
        ),
    )
]

# Discovery for /mcp. A client connecting https://host/mcp asks, per RFC 9728,
# for /.well-known/oauth-protected-resource/mcp, and the 401 names the bare
# /.well-known/oauth-protected-resource. Mounted at the root, the SDK's own
# metadata route would answer the second with the origin as the resource (not
# .../mcp), and nothing would answer the first -- found live on
# MCP_Data_Analyst. The bridge's metadata is the one consistent with its
# authorization server at the root, so both paths go to it.
_domain_discovery = (
    []
    if _domain_bridge is None
    else [
        Route("/.well-known/oauth-protected-resource/mcp", _domain_bridge.protected_resource),
        Route("/.well-known/oauth-protected-resource", _domain_bridge.protected_resource),
    ]
)

app = Starlette(
    routes=[
        Route("/health", _root_health),
        Route("/version", _root_version),
        Route("/", _root),
        # Off unless MCP_UPLOAD_URLS=1 and MCP_UPLOAD_BASE_URL are set, and then
        # outside the tiers' bearer auth: the signed, single-use token in the
        # path is the credential (shared/exchange.py, upload URLs).
        Route("/upload/{token}", upload_route, methods=["PUT", "POST"]),
        *_discovery_redirects,
        *_domain_discovery,
        *(Mount(f"/{name}", app=sub_app) for name, sub_app in _sub_apps.items()),
        # Last, so every route above wins: /mcp and the domain server's own
        # OAuth routes answer at the root.
        Mount("", app=_domain_app),
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
    # Serving over HTTP means the caller is remote and has no business naming
    # a path outside the data folder. On by default here as well as in
    # docker-compose.yml, so any deployment is confined.
    os.environ.setdefault("MCP_CONFINE_PATHS", "1")
    uvicorn.run(app, host=args.host, port=args.port, timeout_keep_alive=keepalive)


if __name__ == "__main__":
    main()
