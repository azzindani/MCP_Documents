"""assemble() -- one tool for what a PDF site shows as six buttons.

Merge, split, extract pages, remove pages, organise and rotate are not six
operations. They are one -- *produce a document from a page selection* -- and
the six-button split is a property of user interfaces, where a button cannot
take an argument. An agent's verb can.

    assemble(["a.pdf", "b.pdf"], "a:1-5, b:all, a:9r90", out)

        merge    several sources
        split    call it twice with complementary selections
        remove   select the complement
        reorder  list the pages in the order you want them
        rotate   the r90 / r180 / r270 suffix

It also interleaves, which no button does.

**This is not the banned ops-dict pattern.** The vocabulary is one documented
grammar in one typed `str`, it is parsed by code that can be tested alone, and
the parse is ECHOED BACK in the response so a caller can see what was
understood before trusting the output. A grammar whose interpretation cannot be
inspected is a dict of dicts wearing a different hat.
"""

from __future__ import annotations

import re
from pathlib import Path

import pikepdf

from core.formatter import fail, ok
from core.paths import PathError, finish, require_pdf, resolve_out, resolve_source
from core.selection import SelectionError, parse_ordered
from shared.progress import info

OP = "assemble"

# <key>:<range>[rotation]
_CLAUSE = re.compile(r"^(?P<key>[^:]+):(?P<range>all|[\d,\-\s]+?)(?:r(?P<rot>90|180|270))?$", re.IGNORECASE)


class GrammarError(ValueError):
    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


def parse_selection(select: str, sources: list[str], page_counts: dict[str, int]) -> list[dict]:
    """Turn "a:1-5, b:all, a:9r90" into an ordered list of clauses.

    Keys are basenames without extension, or s0/s1/... by index when basenames
    collide. Both are accepted always, so a caller that used indices does not
    have to know whether the names happened to be unique.
    """
    keys = _keys_for(sources)
    if not select.strip():
        raise GrammarError(
            "No selection given.",
            "Say which pages to take, e.g. select='" + ", ".join(f"{name}:all" for name in list(keys)[:2]) + "'.",
        )

    clauses: list[dict] = []
    for raw in select.split(","):
        # A range like "1-5, 9" contains a comma, so a clause that has no colon
        # continues the previous one's source rather than being an error.
        piece = raw.strip()
        if not piece:
            continue
        if ":" not in piece and clauses:
            piece = f"{clauses[-1]['key']}:{piece}"
        clauses.append(_clause(piece, select, keys, page_counts))
    if not clauses:
        raise GrammarError(f"Nothing selected by {select!r}.", "Use e.g. select='doc:1-5'.")
    return clauses


def _keys_for(sources: list[str]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for index, source in enumerate(sources):
        keys[f"s{index}"] = source
        stem = Path(source).stem
        # First one wins on a collision, and the index form always works, so a
        # duplicate basename degrades to "use s0/s1" rather than to a silent
        # wrong file.
        keys.setdefault(stem, source)
    return keys


def _clause(piece: str, whole: str, keys: dict[str, str], page_counts: dict[str, int]) -> dict:
    match = _CLAUSE.match(piece)
    if not match:
        raise GrammarError(
            f"Cannot read {piece!r} in select={whole!r}.",
            "Each clause is <source>:<pages>[r90], e.g. 'report:1-5' or 'report:9r90'.",
        )
    key = match.group("key").strip()
    if key not in keys:
        raise GrammarError(
            f"{key!r} is not one of the sources.",
            "Use one of: " + ", ".join(sorted(keys)) + ".",
        )
    source = keys[key]
    spec = match.group("range").strip()
    spec = "" if spec.lower() == "all" else spec
    try:
        # parse_ordered, not parse_pages: reordering and duplicating pages are
        # real operations here, and the set-flattening in parse_pages would
        # quietly discard exactly what assemble exists to do.
        pages = parse_ordered(spec, page_counts[source])
    except SelectionError as exc:
        raise GrammarError(str(exc), exc.hint) from exc
    return {"key": key, "source": source, "pages": pages, "rotate": int(match.group("rot") or 0)}


def assemble(sources: list[str], select: str, out: str) -> dict:
    """Build a document from a page selection: merge, split, reorder, rotate."""
    progress: list[dict] = []
    if not sources:
        return fail(OP, "No sources given.", "Pass at least one path in sources=[...].", progress)

    resolved: list[str] = []
    counts: dict[str, int] = {}
    try:
        for source in sources:
            found = resolve_source(source)
            require_pdf(found, "assemble")
            path = str(found)
            with pikepdf.open(path) as handle:
                counts[path] = len(handle.pages)
            resolved.append(path)
    except PathError as exc:
        return fail(OP, str(exc), exc.hint, progress)
    except pikepdf.PdfError as exc:
        return fail(
            OP,
            f"Could not open a source: {exc}",
            "Try optimize(action='repair') on it first.",
            progress,
        )

    try:
        clauses = parse_selection(select, resolved, counts)
    except GrammarError as exc:
        return fail(OP, str(exc), exc.hint, progress)

    try:
        destination = resolve_out(out, Path(resolved[0]))
    except PathError as exc:
        return fail(OP, str(exc), exc.hint, progress)
    written = 0
    try:
        target = pikepdf.Pdf.new()
        open_handles: dict[str, pikepdf.Pdf] = {}
        for clause in clauses:
            handle = open_handles.get(clause["source"])
            if handle is None:
                handle = pikepdf.open(clause["source"])
                open_handles[clause["source"]] = handle
            for number in clause["pages"]:
                page = target.pages.append(handle.pages[number - 1]) or target.pages[-1]
                if clause["rotate"]:
                    page.rotate(clause["rotate"], relative=True)
                written += 1
        target.save(destination)
        for handle in open_handles.values():
            handle.close()
        finish(destination)
    except (pikepdf.PdfError, OSError) as exc:
        return fail(
            OP, f"Could not write {out!r}: {exc}", "Check the output directory exists and is writable.", progress
        )

    progress.append(info(f"wrote {written} page(s) from {len({c['source'] for c in clauses})} source(s)"))
    result = {
        "out": str(destination),
        "pages_written": written,
        # The parse, echoed back. Without this the grammar is exactly the
        # opaque vocabulary the ops-dict pattern is banned for: a caller could
        # not tell a misread selection from a correct one until they opened the
        # output.
        "parsed": [
            {
                "source": Path(clause["source"]).name,
                "pages": clause["pages"],
                "rotate": clause["rotate"],
            }
            for clause in clauses
        ],
    }
    return ok(OP, result, progress)
