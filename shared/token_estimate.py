"""Report what a response actually costs, instead of a number typed in by hand.

Every tool returns `token_estimate`, and CLAUDE.md defines it as
`len(str(response)) // 4`. Across this repo it was a literal instead: **325**
occurrences of `"token_estimate": 15` and friends, the largest count in the
fleet, none of them measured.

Under-reporting is the direction that hurts. A client budgets its context from
this number and *admits* the response on the strength of it, so an
order-of-magnitude undercount blows the ~12,000-token budget these servers are
designed around. Error responses are the worst case: their length is dominated
by a variable-length message, so any constant is wrong by construction -- and
*improving* a message by making it more specific silently makes the lie bigger.

Measured on the live fleet before this was written: a `read_document` refusal
naming the timestamp route returned `"token_estimate": 15` for a response of
~205. `restore_version`'s "No backups found" returned 20 for ~100.

MCP_Math is the only repo that got this right, in `src/engine/formatter.py`.
This is the same choke point, adapted: 325 hand-edits would drift out of step
with the responses the way the literals already have.

`measure_responses(mcp)` is applied once per server, after the tools are
declared, and changes no tool body. It wraps the tools the manager has already
registered rather than the `@mcp.tool` decorator, so it does not matter whether
it sits above or below the declarations -- a decorator wrapper would have to
precede every one of them and would silently miss any tool added above it.

On fastmcp 2.x the object bound to the module-level name IS the entry in
`_tool_manager._tools`, so wrapping `.fn` is also what the tests see when they
call `mod.some_tool.fn(...)` directly. That is deliberate: a fix the test suite
dispatches around is a fix nobody can hold to account.
"""

from __future__ import annotations

import functools
from typing import Any


def recount(response: Any) -> Any:
    """Set `token_estimate` to the measured size of the response carrying it.

    The field is removed before measuring so the number describes the payload
    rather than partly describing itself. Non-dict returns pass through
    untouched -- the return contract is enforced elsewhere, and this is not the
    place to start raising on it.
    """
    if not isinstance(response, dict):
        return response
    response.pop("token_estimate", None)
    response["token_estimate"] = len(str(response)) // 4
    return response


def measure_responses(mcp: Any) -> None:
    """Measure `token_estimate` on every tool this server has registered."""
    for tool in mcp._tool_manager._tools.values():
        fn = getattr(tool, "fn", None)
        if fn is None or getattr(fn, "__token_estimate_measured__", False):
            continue
        tool.fn = _measured(fn)


def _measured(fn: Any) -> Any:
    # functools.wraps carries __name__, __doc__ and __annotations__ over and
    # sets __wrapped__ so inspect.signature follows through to the original --
    # fastmcp validates arguments against that signature on every call, not
    # only at registration.
    @functools.wraps(fn)
    def measured(*a: Any, **kw: Any) -> Any:
        return recount(fn(*a, **kw))

    # Installing twice would double-wrap and cost a needless frame per call;
    # harmless but easy to avoid, and it makes the helper safe to call from a
    # module that is imported more than once.
    measured.__token_estimate_measured__ = True  # type: ignore[attr-defined]
    return measured
