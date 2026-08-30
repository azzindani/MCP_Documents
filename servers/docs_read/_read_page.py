"""read_page() and to_markdown().

`read_page` is the "go and look" tool: everything on one page, bounded by
construction because one page cannot exceed the budget. It is what an agent
calls after `find` says the answer is on page 44.

`to_markdown` is the convenience path for documents that genuinely fit, and a
refusal that names a range for the ones that do not. It exists because most
documents ARE small, and making every caller drive probe -> find -> extract for
a three-page invoice would be a worse tool for the common case.
"""

from __future__ import annotations

from core import budget, clean, order
from core.formatter import fail, ok, refuse
from core.readers import UnsupportedFormat, load_page_words, open_source
from core.readers.pdf import PdfError
from core.selection import SelectionError, format_pages, parse_pages
from shared.progress import info, warn


def read_page(source: str, page: int, password: str = "") -> dict:
    """Read one page: text, tables, links, and how each was obtained."""
    op = "read_page"
    progress: list[dict] = []
    try:
        doc = open_source(source, password)
    except (PdfError, UnsupportedFormat) as exc:
        return fail(op, str(exc), exc.hint, progress)

    if page < 1 or page > doc.page_count:
        return fail(
            op,
            f"Page {page} does not exist; the document has {doc.page_count}.",
            f"Ask for a page in 1-{doc.page_count}.",
            progress,
        )

    loaded = load_page_words(doc, page)
    columns = order.detect_columns(loaded)
    tabular = order.looks_tabular(loaded)
    lines = order.lines_from_blocks(order.order_blocks(loaded, columns))

    # Always look for tables, not only when the layout looks tabular: a small
    # ruled table inside a page of prose does not move the gutter statistics at
    # all, and read_page is the "go and look" tool -- missing a table on the
    # page it was asked to show would defeat the point of calling it.
    import pdfplumber

    from core import tables as table_engine

    with pdfplumber.open(doc.source, password=doc.password) as plumbed:
        tables = table_engine.extract_page_tables(plumbed.pages[page - 1])

    progress.append(info(f"read page {page} of {doc.page_count}"))
    if loaded.is_scanned:
        progress.append(warn("this page has no text layer", f"run ocr(pages='{page}')"))

    result = {
        "page": page,
        "of": doc.page_count,
        "size": [round(loaded.width, 1), round(loaded.height, 1)],
        "rotation": loaded.rotation,
        "columns": columns,
        "looks_tabular": tabular,
        "text": "\n".join(lines),
        "words": len(loaded.blocks),
        "tables": tables,
    }
    return ok(op, result, progress, basis=loaded.basis)


def to_markdown(source: str, pages: str = "", password: str = "") -> dict:
    """Convert a document to markdown. Refuses when over the token budget."""
    op = "to_markdown"
    progress: list[dict] = []
    try:
        doc = open_source(source, password)
        wanted = parse_pages(pages, doc.page_count)
    except (PdfError, UnsupportedFormat) as exc:
        return fail(op, str(exc), exc.hint, progress)
    except SelectionError as exc:
        return fail(op, str(exc), exc.hint, progress)

    from servers.docs_read._read_extract import _estimate_tokens

    estimated = _estimate_tokens(doc, wanted)
    ceiling = budget.max_response_tokens()
    if estimated > ceiling:
        fits = max(1, int(len(wanted) * ceiling / estimated))
        return refuse(
            op,
            f"{len(wanted)} page(s) is about {estimated:,} tokens, over the {ceiling:,} limit.",
            f"Use to_markdown(pages='{format_pages(wanted[:fits])}'), "
            "or find() to locate what you need and extract() to read it.",
            limit=f"{ceiling} tokens",
            seen=f"~{estimated} tokens",
            progress=progress,
        )

    sized_by_page: dict[int, list[tuple[str, float]]] = {}
    for number in wanted:
        loaded = load_page_words(doc, number)
        blocks = order.order_blocks(loaded, order.detect_columns(loaded))
        sized_by_page[number] = order.lines_with_size(blocks)

    # Clean on the text alone, then re-attach sizes by position. Cleaning can
    # only remove whole lines (furniture) or join two into one (hyphens), so
    # matching cleaned text back to the size of the line it came from is a
    # lookup, not a guess -- and doing it this way keeps clean.py unaware that
    # font sizes exist.
    lines_by_page = {n: [text for text, _ in pairs] for n, pairs in sized_by_page.items()}
    text, cleaned = clean.clean_pages(lines_by_page)
    size_of = {t.strip(): sz for pairs in sized_by_page.values() for t, sz in pairs if t.strip()}
    body = _mark_headings(text, size_of)

    progress.append(info(f"converted {len(wanted)} page(s)"))
    result = {
        "markdown": body,
        "pages": format_pages(wanted),
        "characters": len(body),
        "headings": body.count("\n#") + body.startswith("#"),
        "cleaned": cleaned,
    }
    # Headings inferred from type size, not from a structure tree. That is the
    # weakest signal in the IR's vocabulary and the response says so rather
    # than presenting an outline as if the document had declared one.
    return ok(op, result, progress, basis="font_size" if "#" in body else "text_layer")


# A line must be this much larger than the document's body size to be a
# heading. 1.15 is deliberately conservative: over-marking headings turns a
# document into an outline of nonsense, and a missed heading is still readable
# prose.
HEADING_RATIO = 1.15

# A "heading" longer than this is a pull quote or a title page paragraph. Real
# headings are short; marking a 400-character paragraph as one produces an
# outline that is worse than no outline.
MAX_HEADING_CHARS = 120


def _mark_headings(text: str, size_of: dict[str, float]) -> str:
    """Prefix lines set noticeably larger than the body with a markdown hash.

    Font size is a guess -- `font_size` is the IR's own name for it and one of
    the two lowest-confidence bases there is. A PDF carrying a real structure
    tree would answer better; most carry none.

    Deliberately conservative in both directions. The threshold is a ratio
    against the MEDIAN line size, so a document set entirely in 18pt has no
    headings rather than all of them; and a line is only upgraded if it is
    short, because a full paragraph in a large face is a pull quote, not a
    heading. Over-marking turns a document into an outline of nonsense, while a
    missed heading is still readable prose.
    """
    sizes = [size for size in size_of.values() if size]
    if not sizes:
        return text
    body_size = sorted(sizes)[len(sizes) // 2]
    threshold = body_size * HEADING_RATIO

    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        size = size_of.get(stripped, 0.0)
        if stripped and size > threshold and len(stripped) <= MAX_HEADING_CHARS:
            level = 1 if size >= body_size * 1.5 else 2
            out.append(f"{'#' * level} {stripped}")
        else:
            out.append(line)
    return "\n".join(out)
