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
from core.ir import Basis, Document, as_basis
from core.readers import (
    ReaderError,
    bookmarks,
    capability,
    load_page,
    load_page_words,
    open_source,
    ruled_pages,
)
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
        doc = open_source(source, password)
    except ReaderError as exc:
        return fail(OP, str(exc), exc.hint, progress)

    # Deliberately NOT closed here. `open_source` puts the document in the LRU
    # in core/cache.py, which owns closing it on eviction -- and the intended
    # probe -> find -> extract sequence depends on the next call finding it
    # still open. Closing it here left a CLOSED document in the cache, so the
    # following tool got a handle that raised on first use; every read tool
    # apart from this one already relied on the cache.
    return _describe(doc, progress)


def _describe(doc: Document, progress: list[dict]) -> dict:
    scanned: list[int] = []
    digital: list[int] = []
    empty: list[int] = []
    total_chars = 0

    # Three buckets, and they are MUTUALLY EXCLUSIVE. A page used to be put in
    # both `empty` and `scanned` and the count then subtracted one from the
    # other, so `page_kinds.scanned` was zero for every ordinary scan -- a real
    # one extracts exactly 0 characters, and only a page in the 1..31 band
    # could survive the subtraction. A 1 MB scanned invoice reported
    # `{born_digital: 0, scanned: 0, empty: 1}` beside `scanned_pages: "1"`:
    # two fields of one response disagreeing, and the one a caller branches on
    # to decide whether to OCR was the wrong one.
    #
    # What separates the last two is ink, not characters. `has_content` is the
    # reader's answer to "is there anything here OCR could read".
    for number in range(1, doc.page_count + 1):
        page = load_page(doc, number)
        total_chars += page.char_count
        if not page.is_scanned:
            digital.append(number)
        elif page.has_content:
            scanned.append(number)
        else:
            empty.append(number)
    progress.append(info(f"read the text layer of {doc.page_count} pages"))

    sample = _sample(doc.page_count)
    # Empty for every format whose tables are real markup rather than ink. The
    # router answers that rather than each reader stubbing it, and the response
    # omits the whole `tables` section instead of reporting zero -- "0 pages
    # with ruling lines" for an HTML file reads as "no tables here", which is
    # a different and wrong claim.
    ruled = len(ruled_pages(doc, sample))
    has_ruling = capability(doc, "ruled_pages") is not None
    sampled_all = len(sample) == doc.page_count
    if has_ruling:
        progress.append(
            info(f"checked {len(sample)} of {doc.page_count} pages for ruling lines")
            if not sampled_all
            else info("checked every page for ruling lines")
        )

    tokens_full = budget.estimate_tokens("x" * total_chars)
    fits = budget.pages_that_fit(total_chars, doc.page_count)

    # `extractable` is about TEXT, so a blank page counts against it exactly as
    # a scanned one does -- neither yields any. Splitting the two buckets apart
    # above meant this had to stop asking only about `scanned`, or a document
    # of blank pages would have reported `extractable: "full"`.
    without_text = sorted(scanned + empty)
    if without_text and not digital:
        extractable = "none"
    elif without_text:
        extractable = "partial"
    else:
        extractable = "full"
    if extractable != "full":
        progress.append(warn(f"{len(without_text)} page(s) have no text layer", f"pages {format_pages(without_text)}"))

    first = doc.pages.get(1)
    result = {
        "format": doc.format,
        "pages": doc.page_count,
        "bytes": doc.meta.get("bytes", 0),
        "encrypted": doc.encrypted,
        # None, not [0.0, 0.0], for a format with no geometry. A page size of
        # zero is a measurement that reads as real; None says there is nothing
        # to measure, which for HTML or an email body is the truth.
        "page_size": [round(first.width, 1), round(first.height, 1)] if first and first.width else None,
        # Whether the page numbers in every other response mean anything in the
        # file itself. "synthetic" for the flow formats -- HTML, text, Word,
        # email -- where this server divided a continuous document into pages
        # of its own choosing, and says how big it made them.
        "pagination": doc.meta.get("pagination", "native"),
        # The three counts sum to page_count, because the buckets above are
        # exclusive. They used to overlap and be subtracted back apart.
        "page_kinds": {
            "born_digital": len(digital),
            "scanned": len(scanned),
            "empty": len(empty),
        },
        # A selection string rather than a list: it is meant to be pasted
        # straight into ocr(pages=...) or extract(pages=...), and a 200-element
        # list of page numbers is both unreadable and expensive in context.
        "scanned_pages": format_pages(scanned),
        "digital_pages": format_pages(digital),
        "extractable": extractable,
        # The number that tells a caller why they must not ask for it all.
        "token_estimate_full": tokens_full,
        "pages_that_fit_one_response": fits,
    }
    if result["pagination"] == "synthetic":
        result["chars_per_page"] = doc.meta.get("chars_per_page")
    if has_ruling:
        result["tables"] = {
            "pages_with_ruling_lines": ruled,
            "sampled_pages": len(sample),
            "sample_is_every_page": sampled_all,
        }

    # Whatever this format knows about itself that the others do not: a web
    # page's title and link count, a workbook's sheet names, a message's
    # sender. Kept behind a capability so the shared result stays the same
    # shape for every format and the extras are visibly extras.
    extras = capability(doc, "probe_extras")
    if extras:
        result["format_details"] = extras(doc)
    return ok(OP, result, progress, basis=_document_basis(doc, digital))


def _document_basis(doc: Document, digital: list[int]) -> Basis:
    """The basis the pages actually carry, not a constant.

    This used to return `"text_layer"` for anything not entirely scanned, which
    is right for a PDF and wrong for every other format here. `text_layer`
    means "glyphs the file actually contains" -- the correct claim about a PDF,
    whose paragraphs and tables are then RECONSTRUCTED from coordinates. HTML,
    Word, slides, worksheets, email, epub and XBRL all declare their structure,
    so their readers set `native`, a STRONGER claim. probe threw that away and
    reported the weaker one for twelve formats.

    It matters most for the format that led to finding it. An XBRL fact is a
    number the filer tagged in a machine-readable field; reporting it under the
    same basis as a figure recovered from glyph positions says the two are
    equally reliable, and the entire purpose of this field is that they are not.

    Same shape as `outline`'s: use what the pages agree on, and fall back to the
    conservative answer where they disagree rather than picking a winner.
    """
    if not digital:
        return "empty"
    found = {doc.pages[number].basis for number in digital if number in doc.pages}
    return as_basis(found.pop(), "text_layer") if len(found) == 1 else "text_layer"


# A line must be this much larger than the body to be an inferred heading, and
# no longer than this. Same constants as to_markdown's, and deliberately the
# same numbers: two tools disagreeing about what a heading is would be the
# fleet's most repeated defect -- a formula written down twice whose copies
# drifted -- so this imports them rather than restating them.
def outline(source: str, password: str = "") -> dict:
    """List headings and bookmarks with page anchors. Use before extract."""
    op = "outline"
    progress: list[dict] = []
    try:
        doc = open_source(source, password)
    except ReaderError as exc:
        return fail(op, str(exc), exc.hint, progress)

    entries = bookmarks(doc)
    if entries:
        progress.append(info(f"{len(entries)} heading(s) the document declares itself"))
        # The response basis comes from the ENTRIES, not from a constant. A
        # PDF's bookmarks are `tagged`; HTML, Markdown, Word, slides and epub
        # headings are `native`, which is a stronger claim, and reporting every
        # one of them as `tagged` would misname where the answer came from for
        # five formats out of six.
        found = {str(entry.get("basis", "tagged")) for entry in entries}
        return ok(
            op,
            {"entries": entries, "count": len(entries)},
            progress,
            basis=as_basis(found.pop(), "tagged") if len(found) == 1 else "tagged",
        )

    # Most real PDFs have no bookmarks. Measured across the corpus, four of
    # five did not -- two government regulations, a 68-page contract and a CFR
    # volume all have an empty outline -- so the inferred path is the common
    # case for that format, not the exception, and it has to say it is
    # inferred. The formats that declare their headings never reach here.
    progress.append(info("no declared headings; inferring from type size"))
    entries = _inferred(doc, progress)
    if not entries:
        return ok(
            op,
            {"entries": [], "count": 0},
            progress,
            basis="empty",
            hint="No bookmarks and no headings stand out by size. Use find() to navigate instead.",
        )
    return ok(op, {"entries": entries, "count": len(entries)}, progress, basis="font_size")


# Pages sampled when inferring headings. Reading every page of an 843-page
# volume to build a table of contents costs more than the caller saved by not
# reading the document, and headings cluster near section starts anyway.
OUTLINE_SAMPLE_PAGES = 60


def _inferred(doc, progress: list[dict]) -> list[dict]:
    from core import order
    from servers.docs_read._read_page import HEADING_RATIO, MAX_HEADING_CHARS

    numbers = _sample(doc.page_count, OUTLINE_SAMPLE_PAGES)
    if len(numbers) < doc.page_count:
        progress.append(info(f"sampled {len(numbers)} of {doc.page_count} pages"))

    sized: list[tuple[int, str, float]] = []
    for number in numbers:
        page = load_page_words(doc, number)
        blocks = order.order_blocks(page, order.detect_columns(page))
        for text, size in order.lines_with_size(blocks):
            if text.strip():
                sized.append((number, text.strip(), size))
    if not sized:
        return []

    sizes = sorted(size for _, _, size in sized if size)
    if not sizes:
        return []
    body_size = sizes[len(sizes) // 2]
    threshold = body_size * HEADING_RATIO

    return [
        {
            "level": 1 if size >= body_size * 1.5 else 2,
            "title": text,
            "page": number,
            "basis": "font_size",
        }
        for number, text, size in sized
        if size > threshold and len(text) <= MAX_HEADING_CHARS
    ]
