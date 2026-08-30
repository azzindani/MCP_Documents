"""Parsing and printing page-selection strings: "1-5,9,40-".

Every tool that takes a `pages` argument uses this, and `assemble`'s grammar is
built on top of it. One parser, one set of error messages, one place where
1-based user page numbers meet 0-based indexing.

The errors here name the value the caller wrote and the range that exists. An
error that quotes back the tool's own default -- `Column '' not found` -- is the
commonest defect shape in this fleet, in three repos, and it happens precisely
because a parser reports on a value the caller never supplied.
"""

from __future__ import annotations

import re

_CLAUSE = re.compile(r"^(\d+)(?:\s*-\s*(\d*))?$")


class SelectionError(ValueError):
    """Raised with a message that names the input and the valid range."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


def parse_pages(spec: str, page_count: int) -> list[int]:
    """Turn "1-5,9,40-" into [1,2,3,4,5,9,40,…] against a known page count.

    An empty spec means every page. Pages are 1-based and inclusive, matching
    what a person sees in a viewer; "40-" means 40 to the end. Duplicates are
    dropped and the result is sorted, so "3,1,1-2" is [1,2,3] -- a selection is
    a set of pages, not a sequence. `assemble` needs ORDER and therefore does
    not use this function; see parse_ordered().
    """
    if page_count < 1:
        raise SelectionError("The document has no pages.", "Call probe() to check the document opened.")
    if not spec.strip():
        return list(range(1, page_count + 1))

    out: set[int] = set()
    for raw in spec.split(","):
        clause = raw.strip()
        if not clause:
            continue
        out.update(_expand(clause, spec, page_count))
    if not out:
        raise SelectionError(
            f"Page selection {spec!r} selected no pages.",
            f"Use a range inside 1-{page_count}, for example pages='1-{min(20, page_count)}'.",
        )
    return sorted(out)


def parse_ordered(spec: str, page_count: int) -> list[int]:
    """Like parse_pages, but keeps order and duplicates: "3,1,1" -> [3,1,1].

    Reordering and page duplication are real operations -- assemble() exists to
    do them -- so the set-flattening in parse_pages would quietly discard the
    caller's intent.
    """
    if not spec.strip():
        return list(range(1, page_count + 1))
    out: list[int] = []
    for raw in spec.split(","):
        clause = raw.strip()
        if clause:
            out.extend(_expand(clause, spec, page_count))
    return out


def _expand(clause: str, whole: str, page_count: int) -> list[int]:
    match = _CLAUSE.match(clause)
    if not match:
        raise SelectionError(
            f"Cannot read {clause!r} in page selection {whole!r}.",
            "Use numbers and ranges: pages='3', pages='1-20', pages='1-5,9,40-'.",
        )
    start = int(match.group(1))
    if match.group(2) is None:
        end = start
    elif match.group(2) == "":
        end = page_count  # "40-" means to the end
    else:
        end = int(match.group(2))

    if start < 1:
        raise SelectionError(
            f"Page {start} in {whole!r} is below 1; pages are numbered from 1.",
            f"This document has pages 1-{page_count}.",
        )
    if start > page_count or end > page_count:
        raise SelectionError(
            f"Page selection {whole!r} asks for page {max(start, end)}, but the document has {page_count}.",
            f"Use a range inside 1-{page_count}.",
        )
    if end < start:
        raise SelectionError(
            f"Range {clause!r} in {whole!r} ends before it starts.",
            f"Write it as '{end}-{start}' if that is what you meant.",
        )
    return list(range(start, end + 1))


def format_pages(pages: list[int]) -> str:
    """Collapse [1,2,3,9,40,41] back to "1-3,9,40-41".

    Used wherever a response reports a set of pages -- probe()'s scanned_pages
    is meant to be pasted straight into ocr(pages=…), so it has to come back
    out in the syntax the parser accepts. A round trip through this pair is the
    cheapest possible test of both.
    """
    if not pages:
        return ""
    ordered = sorted(set(pages))
    runs: list[tuple[int, int]] = []
    start = prev = ordered[0]
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
            continue
        runs.append((start, prev))
        start = prev = n
    runs.append((start, prev))
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)
