"""Verify that every @mcp.tool() docstring is <= 80 characters.

The docstring is not documentation here, it is the whole contract: a live MCP
schema carries no enums and no parameter descriptions, so every string argument
reaches the model as a bare {"type": "string"} and the one sentence above it is
all there is. Over 80 characters it stops being read as a signature and starts
costing context in a 10,000-token budget, thirteen times over.
"""

from __future__ import annotations

import ast
import pathlib
import sys

errors = []
checked = 0
for f in pathlib.Path("servers").rglob("server.py"):
    tree = ast.parse(f.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        has_tool = any(
            (isinstance(d, ast.Attribute) and d.attr == "tool")
            or (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "tool")
            for d in node.decorator_list
        )
        if not has_tool:
            continue
        checked += 1
        doc = ast.get_docstring(node) or ""
        if not doc:
            errors.append(f"{f}:{node.lineno} {node.name}: no docstring, so the tool has no contract")
        elif len(doc) > 80:
            errors.append(f"{f}:{node.lineno} {node.name}: {len(doc)} chars > 80")

# A verifier that finds nothing to check passes silently and forever. This has
# happened in this fleet: a layout change moved the tools and the script kept
# reporting success against an empty set.
if checked == 0:
    print("No @mcp.tool() functions found under servers/ -- the verifier is looking in the wrong place.")
    sys.exit(1)

if errors:
    print("Tool docstring violations:")
    for e in errors:
        print(" ", e)
    sys.exit(1)

print(f"All {checked} tool docstrings within the 80 char limit.")
