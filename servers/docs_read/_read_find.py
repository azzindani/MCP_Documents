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

from core import budget, scan
from core.formatter import fail, ok, refuse
from core.ir import weakest_basis
from core.readers import ReaderError, load_page, open_source
from core.selection import SelectionError, format_pages, parse_pages
from shared.counts import counted
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
    scanned_without_text = 0
    searchable: list[int] = []
    texts: list[str] = []
    for number in wanted:
        page = load_page(doc, number)
        if page.is_scanned:
            scanned_without_text += 1
            continue
        searchable.append(number)
        texts.append(page.text)

    # A pattern the CALLER wrote runs in a child process that can be killed.
    # `re` has no timeout and no step limit, and `(\s*\w+)+$` against one page
    # of this corpus was still backtracking when it was killed at 120 seconds
    # -- on a deployed server, a worker that never comes back, with nothing in
    # the response or the log to say why. A literal query has been through
    # re.escape, holds no quantifier, and stays in-process. See core/scan.py.
    try:
        hits, raw, where = scan.matches_of(texts, pattern.pattern, pattern.flags, ceiling, guarded=regex)
    except scan.ScanTimeout as exc:
        return refuse(
            OP,
            f"The pattern {query!r} did not finish within {exc.seconds:g}s.",
            "A quantifier inside a quantifier -- (a+)+ , (\\s*\\w+)+ -- backtracks without finishing. "
            "Give the inner part a fixed width, anchor the pattern, or narrow the search with pages=. "
            "A literal search (regex=False) is never affected.",
            limit=f"{exc.seconds:g}s",
            seen=f"{len(texts)} page(s)",
            progress=progress,
        )

    hit_pages = [searchable[index] for index in where]
    matches = [_describe(texts[index], start, end, groups, searchable[index]) for index, start, end, groups in raw]

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
        "pages": format_pages(hit_pages),
        "pages_searched": len(wanted),
        "pages_skipped_no_text": scanned_without_text,
        "matches": matches,
        # A truncated answer that does not say so is the worst kind. The count
        # is exact even when the list is not, so the caller can tell "47 hits,
        # here are 47" from "4,000 hits, narrow it down". Emitted on every
        # response rather than only the cut ones -- the flag used to be set
        # inside the branch below, so a complete answer carried none at all,
        # and an absent flag is not the same as a False one.
        **counted(len(matches), hits),
    }
    if hits > len(matches):
        result["returned_limit"] = ceiling
        # Which of the two limits actually bit. `ceiling` is min(max_hits, what
        # the response budget affords), so past that point raising max_hits
        # changes nothing -- and the hint said to raise it anyway. Asked for
        # 500, 5,000 and 50,000 on a query with 2,360 hits, this returned 400
        # every time and told the caller to ask for more each time.
        if ceiling < max_hits:
            result["hint"] = (
                f"{hits} matches, {ceiling} returned -- the response budget caps this call at {ceiling}, "
                f"so a larger max_hits returns no more. Narrow with pages=, or make the query more specific."
            )
        else:
            result["hint"] = f"{hits} matches, {len(matches)} returned. Narrow with pages=, or raise max_hits."
    # From the pages actually searched, not a constant. `text_layer` is a PDF's
    # answer -- "glyphs the file contains" -- and was returned for every format,
    # including the twelve that declare their structure and answer `native`.
    #
    # And from the pages, NOT from whether anything matched. `basis` says how
    # the text this tool read was obtained; finding no match is a fact about
    # the query, not about the document. Reporting `empty` -- "nothing here to
    # obtain", the only basis worth 0.0 confidence -- for a fruitless search of
    # a 183-page born-digital filing contradicted probe() on the same file in
    # the same session.
    searched = [doc.pages[n].basis for n in searchable if n in doc.pages]
    return ok(OP, result, progress, basis=weakest_basis(searched, fallback="empty"))


def _describe(text: str, start: int, end: int, groups: dict, page: int) -> dict:
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
    if groups:
        entry["groups"] = groups
    return entry
