"""A wrong-typed argument answered with a pydantic dump and a URL.

Every tool in this fleet promises one failure shape: `success: False`, `op`,
`error`, `hint`, `token_estimate`. Round 28 sent one required string parameter
as an int on every endpoint and found four that broke the promise -- browser,
docs-read, docs-edit and math:

    Error executing tool probe: 1 validation error for probeArguments
    source
      Input should be a valid string [type=string_type, input_value=123, input_type=int]
        For further information visit https://errors.pydantic.dev/2.13/v/string_type

No `success` to branch on, no `hint` to act on, no `token_estimate` to budget
with, and a link to the internet from a server whose founding constraint is that
nothing leaves the machine.

Those four are exactly the repos that never received `shared/arg_errors.py`.
The module was written for this, it was already installed on the other three,
and `enforce_known_arguments` -- which looks like it covers this -- does not:
it catches an unknown argument NAME, while a known name with the wrong type is
rejected by pydantic before any of it runs.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _as_text(result) -> str:
    for attr in ("structured_content", "content"):
        value = getattr(result, attr, None)
        if value is not None:
            if isinstance(value, dict):
                return json.dumps(value)
            return json.dumps([getattr(c, "text", str(c)) for c in value])
    return json.dumps(result) if isinstance(result, (dict, list)) else str(result)


@pytest.mark.parametrize(
    "module,tool,args",
    [
        ("servers.docs_read.server", "probe", {"source": 123}),
        ("servers.docs_edit.server", "optimize", {"source": 123}),
    ],
)
def test_a_wrong_type_stays_inside_the_contract(module, tool, args):
    import importlib

    mod = importlib.import_module(module)
    out = asyncio.run(mod.mcp._tool_manager.call_tool(tool, args))
    text = _as_text(out)
    assert "success" in text, text[:400]
    assert "pydantic.dev" not in text, "an offline server sent the caller to the internet"


@pytest.mark.parametrize("module", ["servers.docs_read.server", "servers.docs_edit.server"])
def test_the_refusal_names_the_argument_and_offers_a_hint(module):
    import importlib

    mod = importlib.import_module(module)
    tool = "probe" if "read" in module else "optimize"
    out = asyncio.run(mod.mcp._tool_manager.call_tool(tool, {"source": 123}))
    text = _as_text(out)
    assert "source" in text
    assert "hint" in text


def test_the_unknown_name_guard_still_answers_first():
    """contract_errors goes in first so enforce_known_arguments wraps it."""
    import importlib

    mod = importlib.import_module("servers.docs_read.server")
    out = asyncio.run(mod.mcp._tool_manager.call_tool("probe", {"definitely_not_a_parameter": 1}))
    text = _as_text(out)
    assert "definitely_not_a_parameter" in text
    assert "does not take" in text
