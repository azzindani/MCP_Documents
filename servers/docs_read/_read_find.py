"""find() -- where something is, never what the document says.

The load-bearing tool. A 500-page PDF is ~250,000 tokens against an agent's
~10,000, so the only way to answer a question about one is to locate the answer
and then extract just that. `find` is the locate half, and it must stay cheap:
it returns page numbers, counts and short snippets, and never the content.

With `regex=True` and named groups it is also the custom-parsing path: one
pattern over 300 pages returns rows, and rows are what a sibling data server
loads. That turns "extract every invoice number and total from this bundle"
into a single call whose answer fits in a reply.
"""

from __future__ import annotations

import re

from core import budget
from core.formatter import fail, ok
from core.readers import ReaderError, load_page, open_source
from core.selection import SelectionError, format_pages, parse_pages
from shared.progress import info, warn

OP = "find"

# Characters of context either side of a match. Enough to judge relevance,
# short enough that fifty of them stay inside the response budget.
SNIPPET_CONTEXT = 60


def find(
    source: str,
    query: str,
    regex: bool = False,
    pages: str = "",
    max_hits: int = 50,
    password: str = "",
) -> dict:
    """Locate text across a document. Returns page locations, not content."""
    progress: list[dict] = []
    if not query:
        return fail(OP, "No query given.", "Pass the text to look for, e.g. find(source, query='INVOICE').", progress)

    try:
        pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        return fail(
            OP,
            f"{query!r} is not a valid regular expression: {exc}",
            "Fix the pattern, or pass regex=False to search for it literally.",
            progress,
        )

    try:
        doc = open_source(source, password)
        wanted = parse_pages(pages, doc.page_count)
    except ReaderError as exc:
        return fail(OP, str(exc), exc.hint, progress)
    except SelectionError as exc:
        return fail(OP, str(exc), exc.hint, progress)

    ceiling = min(max_hits, budget.max_response_tokens() // 20)
    matches: list[dict] = []
    hits = 0
    hit_pages: list[int] = []
    scanned_without_text = 0

    for number in wanted:
        page = load_page(doc, number)
        if page.is_scanned:
            scanned_without_text += 1
            continue
        text = page.text
        page_had_hit = False
        for match in pattern.finditer(text):
            hits += 1
            page_had_hit = True
            if len(matches) < ceiling:
                matches.append(_describe(match, text, number))
        if page_had_hit:
            hit_pages.append(number)

    progress.append(info(f"searched {len(wanted)} page(s)"))
    if scanned_without_text:
        progress.append(
            warn(
                f"{scanned_without_text} page(s) have no text layer and were not searched",
                "run ocr() on them first",
            )
        )

    result = {
        "hits": hits,
        "returned": len(matches),
        "pages": format_pages(hit_pages),
        "pages_searched": len(wanted),
        "pages_skipped_no_text": scanned_without_text,
        "matches": matches,
    }
    # A truncated answer that does not say so is the worst kind. The count is
    # exact even when the list is not, so the caller can tell the difference
    # between "47 hits, here are 47" and "4,000 hits, narrow it down".
    if hits > len(matches):
        result["truncated"] = True
        result["hint"] = f"{hits} matches, {len(matches)} returned. Narrow with pages=, or raise max_hits."
    return ok(OP, result, progress, basis="text_layer")


def _describe(match: re.Match, text: str, page: int) -> dict:
    start, end = match.span()
    left = max(0, start - SNIPPET_CONTEXT)
    right = min(len(text), end + SNIPPET_CONTEXT)
    snippet = text[left:right].replace("\n", " ").strip()
    entry: dict = {
        "page": page,
        "line": text.count("\n", 0, start) + 1,
        "snippet": ("…" if left else "") + snippet + ("…" if right < len(text) else ""),
    }
    # Named groups are the custom-parsing path: one pattern over 300 pages
    # returns rows rather than prose. Only named ones -- numbered groups are
    # positional noise in a JSON response.
    groups = {name: value for name, value in (match.groupdict() or {}).items() if value is not None}
    if groups:
        entry["groups"] = groups
    return entry
