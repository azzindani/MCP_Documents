"""What a client may assume about each tool, per the MCP spec.

Every tool on these servers shipped with no annotations at all. That is not
neutral: absent the field, a client applies the spec defaults --

    readOnlyHint     false
    destructiveHint  true
    idempotentHint   false
    openWorldHint    true

-- so `inspect_dataset`, which opens a CSV and returns its shape, advertised
itself as a destructive, non-repeatable operation that reaches the open
internet. A client that gates destructive tools behind a confirmation prompts
for every read; one that trusts openWorldHint believes these servers call out
to the network, which is the opposite of what this project is built on.

The three sibling repos (Math, Machine_Learning, File_System) already declare
theirs. This brings the fourth in line.

The classification was settled by observation rather than by reading names:
each candidate was called against a seeded workspace and the directory
fingerprinted before and after. A tool that *can* write -- even only when given
an optional `output_path` -- is not read-only, because the hint describes the
tool and not one particular call.

    READS    touches nothing on disk, under any arguments
    CREATES  can write a new file, never the path it was given
    EDITS    can write back over the path it was given (snapshots first)

`openWorldHint` is False throughout: these servers are offline-first by
construction and no tool reaches a network at runtime.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

READS: ToolAnnotations = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

CREATES: ToolAnnotations = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

# Not idempotent: these apply operations to a dataset, and applying the same
# ops twice does not generally leave the same file (a patch that adds a column
# or drops rows compounds).
EDITS: ToolAnnotations = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)

__all__ = ["CREATES", "EDITS", "READS"]
