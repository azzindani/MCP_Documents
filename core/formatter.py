"""Build the one response shape every tool returns.

Two rules that are easy to state and were expensive to learn:

`success` is a claim about the CALL, not about whether an exception was raised.
A sibling repo returned `{"success": true, "result": "zoo"}` for 1/0 -- the
serialization of a non-answer was decided carefully and nobody asked whether the
call had succeeded. If a tool cannot do what was asked, `success` is False even
when nothing threw.

`hint` must name a specific tool, parameter or value. "Invalid input." is not a
hint. And the hint must be computed from what actually went wrong, not chosen at
the call site: one catch-all hint per tool is wrong for every failure it was not
written for, which is the single commonest defect shape across this fleet.

`token_estimate` is filled in here as a floor, then recomputed for real by
shared/token_estimate.measure_responses(), which wraps every tool at the server
boundary. Doing it in both places is deliberate -- the engine is testable
without a server, and the wrapper is what guarantees no tool can forget.
"""

from __future__ import annotations

from typing import Any

from core.ir import Basis


def ok(
    op: str,
    result: Any,
    progress: list[dict] | None = None,
    basis: Basis | None = None,
    **extra: Any,
) -> dict:
    """A successful response. `basis` is required for anything extracted."""
    response: dict[str, Any] = {"success": True, "op": op, "result": result}
    if basis is not None:
        response["basis"] = basis
    response.update(extra)
    response["progress"] = progress or []
    response["token_estimate"] = len(str(response)) // 4
    return response


def fail(op: str, error: str, hint: str, progress: list[dict] | None = None, **extra: Any) -> dict:
    """A failed response. Both `error` and `hint` are required, always.

    `error` says what happened; `hint` says what to do about it. A hint that
    only restates the error has not been written yet.
    """
    response: dict[str, Any] = {"success": False, "op": op, "error": error, "hint": hint}
    response.update(extra)
    response["progress"] = progress or []
    response["token_estimate"] = len(str(response)) // 4
    return response


def refuse(op: str, error: str, hint: str, limit: str, seen: str, **extra: Any) -> dict:
    """A budget refusal: too big, too slow, too many pixels.

    Distinct from `fail` because the caller did nothing wrong and there IS a
    way through -- the hint carries it. `limit` and `seen` are separate fields
    so a client can show the gap without parsing prose, and so a test can
    assert the refusal fired for the reason it claims.
    """
    return fail(op, error, hint, refused="budget", limit=limit, seen=seen, **extra)


# How much to trust each basis, as a number a caller can filter on. Kept beside
# the responses that carry it rather than in ir.py, because it is a
# presentation decision: the IR records HOW something was obtained, and this
# says what that is worth.
BASIS_CONFIDENCE: dict[str, float] = {
    "text_layer": 1.0,
    "tagged": 1.0,
    "native": 1.0,
    "ruled": 0.95,
    "ocr": 0.75,  # replaced by the engine's real per-page score where known
    "whitespace": 0.5,
    "font_size": 0.5,
    "empty": 0.0,
}


def confidence_for(basis: Basis, measured: float | None = None) -> float:
    """A measured confidence always wins over the table."""
    return measured if measured is not None else BASIS_CONFIDENCE.get(basis, 0.5)
