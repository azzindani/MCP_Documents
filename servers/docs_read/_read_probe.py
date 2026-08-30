"""probe() -- what is this document, and what can be trusted from it.

The first call in every workflow, and the one that makes the other twelve
usable. A caller that runs `extract` on a 500-page scan without probing first
gets an empty string and no idea why; a caller that probes first is told the
page count, that pages 38-77 have no text layer, and gets that range back as a
string it can paste straight into `ocr(pages=...)`.

Two honesty rules hold this together.

**Per page, never per document.** A hybrid PDF is born-digital on page 1 and a
scan on page 40. A single `scanned: true/false` for the document would be a lie
about half of it, so this returns counts and a selection string.

**Say what was sampled.** Cheap signals (page size, character count) run over
every page, because pypdfium2 is a C library and 500 pages of that is fast.
Expensive signals (ruling lines, which need pdfplumber) run over a SAMPLE, and
the response says so and says how big it was. Reporting "9 tables" from a
20-page sample of a 500-page document, with no mention of the sample, is
precisely the confidently-wrong claim this whole repo is designed against.
"""

from __future__ import annotations

from core import budget
from core.formatter import fail, ok
from core.ir import Document
from core.readers import pdf as pdf_reader
from core.selection import format_pages
from shared.progress import info, warn

OP = "probe"

# How many pages to sample for the signals that need geometry. Twenty is enough
# to say "this document has ruled tables" without turning a probe of a 500-page
# file into a 60-second call, and small enough that the sample is honestly
# reported rather than quietly passed off as a census.
SAMPLE_PAGES = 20


def _sample(page_count: int, size: int = SAMPLE_PAGES) -> list[int]:
    """Evenly spread page numbers, always including the first and last.

    Evenly spread rather than the first N: front matter is not representative
    of a document, and the first twenty pages of a report are a title page, a
    contents page and eighteen pages of preamble.
    """
    if page_count <= size:
        return list(range(1, page_count + 1))
    step = (page_count - 1) / (size - 1)
    return sorted({1 + round(i * step) for i in range(size)})


def probe(source: str, password: str = "") -> dict:
    """Identify a document: format, pages, scanned or digital, what it holds."""
    progress = []
    try:
        doc = pdf_reader.open_document(source, password)
    except pdf_reader.PdfError as exc:
        return fail(OP, str(exc), exc.hint, progress)

    try:
        return _describe(doc, progress)
    finally:
        pdf_reader.close_document(doc)


def _describe(doc: Document, progress: list[dict]) -> dict:
    scanned: list[int] = []
    digital: list[int] = []
    empty: list[int] = []
    total_chars = 0

    for number in range(1, doc.page_count + 1):
        page = pdf_reader.load_page(doc, number)
        total_chars += page.char_count
        if page.char_count == 0:
            empty.append(number)
            scanned.append(number)
        elif page.is_scanned:
            scanned.append(number)
        else:
            digital.append(number)
    progress.append(info(f"read the text layer of {doc.page_count} pages"))

    sample = _sample(doc.page_count)
    ruled = len(pdf_reader.ruled_pages(doc, sample))
    sampled_all = len(sample) == doc.page_count
    progress.append(
        info(f"checked {len(sample)} of {doc.page_count} pages for ruling lines")
        if not sampled_all
        else info("checked every page for ruling lines")
    )

    tokens_full = budget.estimate_tokens("x" * total_chars)
    fits = budget.pages_that_fit(total_chars, doc.page_count)

    if scanned and not digital:
        extractable = "none"
    elif scanned:
        extractable = "partial"
    else:
        extractable = "full"
    if extractable != "full":
        progress.append(warn(f"{len(scanned)} page(s) have no text layer", f"pages {format_pages(scanned)}"))

    first = doc.pages.get(1)
    result = {
        "format": doc.format,
        "pages": doc.page_count,
        "bytes": doc.meta.get("bytes", 0),
        "encrypted": doc.encrypted,
        "page_size": [round(first.width, 1), round(first.height, 1)] if first else None,
        "page_kinds": {
            "born_digital": len(digital),
            "scanned": len(scanned) - len(empty),
            "empty": len(empty),
        },
        # A selection string rather than a list: it is meant to be pasted
        # straight into ocr(pages=...) or extract(pages=...), and a 200-element
        # list of page numbers is both unreadable and expensive in context.
        "scanned_pages": format_pages(scanned),
        "digital_pages": format_pages(digital),
        "extractable": extractable,
        "tables": {
            "pages_with_ruling_lines": ruled,
            "sampled_pages": len(sample),
            "sample_is_every_page": sampled_all,
        },
        # The number that tells a caller why they must not ask for it all.
        "token_estimate_full": tokens_full,
        "pages_that_fit_one_response": fits,
    }
    return ok(OP, result, progress, basis="text_layer" if digital else "empty")
