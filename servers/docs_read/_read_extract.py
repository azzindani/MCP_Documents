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
from core.ir import weakest_basis
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
        suggestion = format_pages(suggest_range(doc, wanted, ceiling, estimated))
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
    # From the pages that yielded text, not a constant. The constant was
    # `text_layer`, which is a PDF's answer and wrong for every format that
    # declares its own structure -- HTML, Word, slides, worksheets, email, epub
    # and XBRL all report `native`, a stronger claim, and this threw it away.
    read_pages = [doc.pages[n].basis for n in wanted if n not in scanned and n in doc.pages]
    basis = "empty" if len(scanned) == len(wanted) else weakest_basis(read_pages)
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
    # more than one kind, so the per-table basis is authoritative and this
    # summarises by taking the WEAKEST -- the claim that is true of every table
    # in the set.
    #
    # This knew only two values. It answered `ruled` when every table was ruled
    # and `whitespace` for anything else, so a table a format DECLARES -- an
    # HTML <table>, a worksheet, an archive manifest, a set of tagged XBRL
    # facts -- was summarised as `whitespace`, the lowest confidence in the
    # vocabulary, on the one kind of table that involves no inference at all.
    # The tables themselves said `native` with confidence 1.0 in the same
    # response, so the summary contradicted its own contents.
    #
    # And it describes what was FOUND, not what survived `min_confidence`. A
    # filter that removes every table left `basis: "empty"` -- "nothing here to
    # obtain", the only basis worth 0.0 confidence -- in the same object as
    # `found_before_filter: 1`. Two fields of one response, flatly
    # contradicting each other, on a page whose table this server had just read
    # at 0.95. `empty` is for a page with nothing on it, not for a page whose
    # tables the caller asked not to see.
    reported = kept or found
    basis = weakest_basis((t["basis"] for t in reported), fallback="whitespace")
    return ok(op, result, progress, basis=basis if reported else "empty")


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


# How many times to re-measure a candidate range before handing it over. Each
# pass strictly shrinks the range, so this terminates; four is far more than
# any real document has needed.
MAX_FIT_PASSES = 4


def suggest_range(doc, wanted: list[int], ceiling: int, estimated: int) -> list[int]:
    """The largest prefix of `wanted` that THIS ESTIMATOR says will fit.

    A budget refusal is a `refuse`, not a `fail`: the caller did nothing wrong
    and the hint carries a value they can use. So the value has to work, and
    the only way to know that is to measure the candidate with the same
    function that will judge their next call.

    Scaling alone does not work, and the failure is systematic rather than
    unlucky. `len(wanted) * ceiling / estimated` spreads the whole range's cost
    evenly and then takes the FRONT of it, and the front of a document is
    denser than its mean -- front matter, dense legal preamble, a contents
    page. Measured on a 238-page regulation: mean 1,358 characters a page but
    1,522 over the first 23, so the refusal said `pages='1-23'`, the caller
    followed it verbatim, and got refused again with `pages='1-20'`.
    """
    fits = max(1, int(len(wanted) * ceiling / estimated)) if estimated else len(wanted)
    for _ in range(MAX_FIT_PASSES):
        if fits <= 1:
            break
        cost = _estimate_tokens(doc, wanted[:fits])
        if cost <= ceiling:
            break
        # Shrink by the measured overshoot, and by at least one page, so a
        # ratio that rounds back to the same number cannot stall.
        fits = max(1, min(fits - 1, int(fits * ceiling / cost)))
    return wanted[:fits]
