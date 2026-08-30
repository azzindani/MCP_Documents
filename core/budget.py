"""The three limits that are part of this server's contract.

A 500-page PDF is roughly 250,000 tokens and the agent driving this has about
10,000. A 300 DPI A4 page is ~25 MB as RGB and the container has 1 GiB. OCR runs
1-3 seconds per page per core and the call times out. None of these are edge
cases; they are the ordinary size of the documents this server exists for.

So every limit refuses with a hint that names a specific value the caller can
use instead. Never a silent sample, never an unannounced truncation. A sibling
repo lost twelve tools at once to an unbudgeted memory cliff -- DBSCAN peaked at
~4.1 GB against a 1 GiB container, took the whole process with it, and the
caller saw only a closed socket, which is indistinguishable from a network
fault.

Every number here is read from the environment at CALL time, not import time,
so MCP_CONSTRAINED_MODE and a redeployment's limits are honoured without a
module reload and tests can monkeypatch without one.
"""

from __future__ import annotations

import os

# Rough but stable: the fleet estimates tokens as characters // 4 everywhere,
# and using the same constant here means a budget refusal and the
# token_estimate in the response cannot disagree about what "big" means.
CHARS_PER_TOKEN = 4

# Bytes per pixel when rendering. PDFium hands back RGB; the alpha channel is
# dropped on the way out, but it exists during the render, so budget for 4.
BYTES_PER_PIXEL = 4


def constrained() -> bool:
    return os.environ.get("MCP_CONSTRAINED_MODE", "0").strip() == "1"


def max_response_tokens() -> int:
    """Ceiling on any single content-returning response."""
    return int(os.environ.get("DOCS_MAX_RESPONSE_TOKENS", "2000" if constrained() else "8000"))


def max_render_bytes() -> int:
    """Ceiling on one render call's peak pixel buffer.

    Deliberately well under the container limit: the process is also holding
    the parsed document, the encoder's own buffers, and whatever the previous
    call left for the collector.
    """
    return int(os.environ.get("DOCS_MAX_RENDER_BYTES", str(64 * 1024 * 1024 if constrained() else 256 * 1024 * 1024)))


def max_ocr_seconds() -> float:
    """Ceiling on one OCR call, in seconds."""
    return float(os.environ.get("DOCS_MAX_OCR_SECONDS", "60" if constrained() else "180"))


def ocr_seconds_per_page() -> float:
    """Tesseract's rate, used to refuse BEFORE starting rather than after.

    Measured on the fixture corpus and overridable, because it varies by an
    order of magnitude with page size, DPI and language. Refusing on an
    estimate is the point: a tool that starts a 14-minute job and is killed at
    120 seconds has spent the time AND lost the work.
    """
    return float(os.environ.get("DOCS_OCR_SECONDS_PER_PAGE", "2.0"))


def ocr_page_timeout() -> float:
    """How long ONE page's Tesseract call may take before being killed.

    Deliberately separate from max_ocr_seconds(), which bounds how many pages a
    call will ACCEPT. Using one number for both looked tidy and was wrong: under
    MCP_CONSTRAINED_MODE=1 the whole-job ceiling is 60s, and the very first OCR
    in a fresh process takes about 114 seconds for a page that then takes 4 --
    the render and recognition stacks both initialise lazily. So a single-page
    job, comfortably inside its own budget, was killed by the timeout meant for
    a hundred-page one.

    The floor of 120s exists for that cold start and does not shrink in
    constrained mode, because a cold start is a cold start regardless of how
    much work the caller is allowed to ask for.

    Overridable, because the right number is a property of the machine rather
    than of this code: the same page took 4 seconds on an idle box and timed
    out at 120 on one under a load average of 10. A test asserting OCR quality
    should pin this rather than inherit whatever the host is doing at the time.
    """
    return float(os.environ.get("DOCS_OCR_PAGE_TIMEOUT", "0")) or max(120.0, max_ocr_seconds())


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def render_bytes(width: float, height: float, dpi: int) -> int:
    """Peak buffer for rendering one page at a DPI. Points are 1/72 inch."""
    return int((width / 72.0 * dpi) * (height / 72.0 * dpi) * BYTES_PER_PIXEL)


def dpi_that_fits(width: float, height: float, pages: int = 1) -> int:
    """The largest whole DPI whose render stays inside the budget.

    Returned in the refusal so the caller gets a value to use rather than
    "too big". Clamped to a floor of 36 -- below that the answer is "render
    fewer pages", not "render at 12 DPI", and a hint suggesting an unusable
    setting is worse than one that names the real constraint.
    """
    budget = max_render_bytes() / max(1, pages)
    inches_sq = (width / 72.0) * (height / 72.0)
    if inches_sq <= 0:
        return 72
    dpi = int((budget / (inches_sq * BYTES_PER_PIXEL)) ** 0.5)
    return max(36, min(dpi, 600))


def pages_that_fit(total_chars: int, page_count: int) -> int:
    """How many pages of this document fit the token ceiling, at its own density.

    Computed from the document actually in hand rather than a guess, so the
    hint says "use pages='1-23'" for a dense report and "pages='1-400'" for a
    sparse one.
    """
    if page_count < 1 or total_chars <= 0:
        return page_count
    per_page = max(1, total_chars // page_count)
    fits = (max_response_tokens() * CHARS_PER_TOKEN) // per_page
    return max(1, min(page_count, fits))


def max_source_bytes() -> int:
    """Ceiling on a file a FLOW reader will parse in one pass.

    PDF does not need this: pypdfium2 opens a 500-page file without reading it,
    and pages arrive as they are asked for. HTML, docx, epub and email have no
    such seam -- the parser builds a tree of the whole document or nothing --
    so the peak memory is a property of the file, not of the request, and the
    only place to refuse is before opening it.

    lxml holds a parsed tree at roughly 5-10x the source bytes, so 64 MiB of
    HTML is most of a 1 GiB container before a single tool has run.
    """
    return int(os.environ.get("DOCS_MAX_SOURCE_BYTES", str(16 * 1024 * 1024 if constrained() else 64 * 1024 * 1024)))


def flow_chars_per_page() -> int:
    """Characters in one synthetic page of a format that has no pages.

    HTML, Markdown, email and text have no page boundaries, but `find` reports
    page numbers, `extract` takes page ranges and every budget here counts in
    pages -- so a flow format needs a page, and the only question is how big.

    2,800 characters, because that is what a real page holds. Measured over 103
    born-digital pages sampled from 25 documents across the corpus: median
    2,822, mean 2,713, p25 1,819, p75 3,631. Picking a round number like 2,000
    or 5,000 would have made `pages_that_fit` and `token_estimate_full` mean
    something different for an HTML file than for the PDF it was converted
    from, which is exactly the kind of quiet disagreement between two answers
    this repo exists to avoid.
    """
    return int(os.environ.get("DOCS_FLOW_CHARS_PER_PAGE", "2800"))


def max_table_pages() -> int:
    """Pages one extract_tables call may scan.

    pdfplumber costs 10-100x more per page than the text layer -- measured on
    the corpus at roughly 5-20 pages/sec against hundreds -- so a whole-document
    table sweep of a 600-page regulation is minutes, not seconds. This is a
    TIME budget wearing a page count, and it is checked before the work starts:
    a call that scans 600 pages and is then killed by the client timeout has
    spent the time and lost the result.
    """
    return int(os.environ.get("DOCS_MAX_TABLE_PAGES", "10" if constrained() else "50"))
