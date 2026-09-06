"""The guard the SDK migration removed, put back.

Every server in this fleet moved to the official `mcp` SDK. Its bundled FastMCP
builds each tool's argument model with pydantic's default `extra="ignore"`, so
an argument no tool declares is dropped and the call succeeds. Two repos --
File_System and Microsoft_Office -- had already written
`enforce_known_arguments` for their own reasons and kept it. Five lost the check
without a line changing, and nothing noticed because nothing failed.

Measured against the deployed endpoints in round 27, an invented argument on
five servers in a row: `success: true` every time. The worst instance was
`apply_patch(output_path=...)`, where the discarded argument meant the caller's
own source file was edited in place while the response said the write had gone
elsewhere.

This asserts the guard is installed here and answers usefully.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp.types import CallToolResult

ROOT = Path(__file__).resolve().parents[1]
_PATHS = (str(ROOT), str(ROOT / "servers" / "docs_read"))
for _p in _PATHS:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import servers.docs_read.server as _server  # noqa: E402

_MCP = _server.mcp


def call(name: str, arguments: dict) -> dict:
    """Through call_tool, never the wrapper's .fn -- the guard lives in call_tool."""
    result = asyncio.run(_MCP._tool_manager.call_tool(name, arguments, convert_result=True))
    # A tool with a structured output schema converts to (content, structured);
    # one without converts to the content list alone. Both reach a client as
    # CallToolResult.content, so both are validated here.
    content = result[0] if isinstance(result, tuple) else result
    CallToolResult(content=list(content))
    return json.loads(content[0].text)


TOOL = "probe"
GOOD = {"source": "/nonexistent/never.pdf"}


class TestAnUndeclaredArgumentIsRefused:
    def test_it_is_refused(self):
        out = call(TOOL, {**GOOD, "definitely_not_a_parameter": 1})
        assert out["success"] is False
        assert "definitely_not_a_parameter" in out["error"]

    def test_the_refusal_names_the_tool(self):
        out = call(TOOL, {**GOOD, "definitely_not_a_parameter": 1})
        assert TOOL in out["error"]

    def test_the_hint_lists_what_is_accepted(self):
        out = call(TOOL, {**GOOD, "definitely_not_a_parameter": 1})
        for key in GOOD:
            assert key in out["hint"]

    def test_a_near_miss_gets_a_suggestion(self):
        wrong = next(iter(GOOD)) + "_"
        out = call(TOOL, {**GOOD, wrong: "x"})
        assert out["success"] is False
        assert "Did you mean" in out["hint"]

    def test_the_response_still_has_the_fleet_failure_shape(self):
        out = call(TOOL, {**GOOD, "definitely_not_a_parameter": 1})
        for field in ("success", "op", "error", "hint", "progress", "token_estimate"):
            assert field in out, f"refusal is missing {field}"

    def test_the_guard_answers_before_the_tool_body_runs(self):
        """The path here does not exist. The refusal must still be about the
        argument name, not about the file -- otherwise the guard is running
        after the work rather than before it."""
        out = call(TOOL, {**GOOD, "definitely_not_a_parameter": 1})
        assert "definitely_not_a_parameter" in out["error"]
        assert "not found" not in out["error"].lower()

    def test_and_the_same_call_without_it_reaches_the_body(self):
        out = call(TOOL, GOOD)
        assert out["success"] is False
        assert "definitely_not_a_parameter" not in out["error"]
