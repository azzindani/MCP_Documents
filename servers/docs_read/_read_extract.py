"""extract() and extract_tables() -- bounded content, with its provenance.

`extract` is the third step of probe -> find -> extract, and the only one that
returns real content. It is bounded by construction: a page range, or a refusal
naming a range that fits. It never returns a whole document just because none
was asked for.

Both tools carry `basis`. A page read from a text layer and a page recovered by
OCR are not the same evidence, and a table found from ruling lines and one
guessed from column gaps are not the same table.
"""

from __future__ import annotations

from core import budget, clean, order
from core.formatter import fail, ok, refuse
from core.readers import ReaderError, load_page, load_page_words, open_source, page_tables
from core.selection import SelectionError, format_pages, parse_pages
from shared.progress import info, warn


def extract(
    source: str,
    pages: str = "",
    clean_text: bool = True,
    password: str = "",
) -> dict:
    """Extract clean text for a page range. Bounded; refuses when too big."""
    op = "extract"
    progress: list[dict] = []
    try:
        doc = open_source(source, password)
        wanted = parse_pages(pages, doc.page_count)
    except ReaderError as exc:
        return fail(op, str(exc), exc.hint, progress)
    except SelectionError as exc:
        return fail(op, str(exc), exc.hint, progress)

    # Estimate before doing the work. A tool that extracts 600 pages and then
    # refuses to return them has spent the time and lost it; the whole point of
    # a budget is to be checked first. The estimate uses the pages the caller
    # asked for, measured on a sample, not a guess about documents in general.
    estimated = _estimate_tokens(doc, wanted)
    ceiling = budget.max_response_tokens()
    if estimated > ceiling:
        fits = max(1, int(len(wanted) * ceiling / estimated))
        suggestion = format_pages(wanted[:fits])
        return refuse(
            op,
            f"Those {len(wanted)} page(s) are about {estimated:,} tokens, over the {ceiling:,} limit.",
            f"Use extract(pages='{suggestion}'), or find() to locate what you need first.",
            limit=f"{ceiling} tokens",
            seen=f"~{estimated} tokens",
            progress=progress,
        )

    lines_by_page: dict[int, list[str]] = {}
    scanned: list[int] = []
    columns_seen: set[int] = set()
    tabular = 0
    for number in wanted:
        page = load_page_words(doc, number)
        if page.is_scanned:
            scanned.append(number)
            lines_by_page[number] = []
            continue
        count = order.detect_columns(page)
        columns_seen.add(count)
        if order.looks_tabular(page):
            tabular += 1
        lines_by_page[number] = order.lines_from_blocks(order.order_blocks(page, count))

    if clean_text:
        text, cleaned = clean.clean_pages(lines_by_page)
    else:
        text = "\n".join(line for number in sorted(lines_by_page) for line in lines_by_page[number])
        cleaned = {}

    progress.append(info(f"extracted {len(wanted) - len(scanned)} page(s) of {len(wanted)}"))
    if scanned:
        progress.append(
            warn(f"{len(scanned)} page(s) have no text layer", f"run ocr(pages='{format_pages(scanned)}') first")
        )

    result = {
        "text": text,
        "pages": format_pages(wanted),
        "characters": len(text),
        "columns": sorted(columns_seen) or [1],
        "pages_without_text": format_pages(scanned),
    }
    if tabular:
        result["tabular_pages"] = tabular
        result["note"] = f"{tabular} page(s) look like tables; extract_tables() reads them as rows"
    if cleaned:
        result["cleaned"] = cleaned
    basis = "empty" if len(scanned) == len(wanted) else "text_layer"
    return ok(op, result, progress, basis=basis)


def extract_tables(
    source: str,
    pages: str = "",
    min_confidence: float = 0.0,
    password: str = "",
) -> dict:
    """Extract tables as rows. Says whether ruling lines or gaps were used."""
    op = "extract_tables"
    progress: list[dict] = []
    try:
        doc = open_source(source, password)
        wanted = parse_pages(pages, doc.page_count)
    except ReaderError as exc:
        return fail(op, str(exc), exc.hint, progress)
    except SelectionError as exc:
        return fail(op, str(exc), exc.hint, progress)

    # pdfplumber costs 10-100x more per page than the text layer, so a
    # whole-document table sweep on a 600-page regulation is minutes. Refuse
    # with a range rather than start it.
    page_cap = budget.max_table_pages()
    if len(wanted) > page_cap:
        return refuse(
            op,
            f"Reading tables on {len(wanted)} pages is far slower than reading text.",
            f"Ask for at most {page_cap} pages, e.g. extract_tables(pages='{format_pages(wanted[:page_cap])}'). "
            "probe() reports which pages have ruling lines.",
            limit=f"{page_cap} pages",
            seen=f"{len(wanted)} pages",
            progress=progress,
        )

    found = page_tables(doc, wanted)

    kept = [t for t in found if t["confidence"] >= min_confidence]
    progress.append(info(f"scanned {len(wanted)} page(s), found {len(found)} table(s)"))
    if len(kept) < len(found):
        progress.append(info(f"{len(found) - len(kept)} below min_confidence={min_confidence}"))

    by_basis: dict[str, int] = {}
    for table in kept:
        by_basis[table["basis"]] = by_basis.get(table["basis"], 0) + 1

    result = {
        "tables": kept,
        "count": len(kept),
        "found_before_filter": len(found),
        "by_basis": by_basis,
        "pages": format_pages(wanted),
    }
    # One basis for the whole response would be a lie whenever a range holds
    # both kinds, so the per-table basis is authoritative and this summarises.
    basis = "ruled" if by_basis.get("ruled") and not by_basis.get("whitespace") else "whitespace"
    return ok(op, result, progress, basis=basis if kept else "empty")


def _estimate_tokens(doc, wanted: list[int]) -> int:
    """Token cost of a page range, from a sample of the range itself.

    Sampled rather than measured, because measuring means reading every page,
    which is the work the budget exists to avoid. Sampled from the RANGE the
    caller asked for, not the document: front matter is not representative, and
    a caller asking for the appendix should be judged on the appendix.
    """
    if not wanted:
        return 0
    step = max(1, len(wanted) // 8)
    sample = wanted[::step][:8]
    chars = 0
    for number in sample:
        chars += load_page(doc, number).char_count
    per_page = chars / len(sample)
    return int(per_page * len(wanted)) // budget.CHARS_PER_TOKEN
