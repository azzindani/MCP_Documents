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
from core.readers import ReaderError, bookmarks, load_page_words, open_source, page_tables
from core.selection import SelectionError, format_pages, parse_pages
from shared.progress import info, warn


def read_page(source: str, page: int, password: str = "") -> dict:
    """Read one page: text, tables, links, and how each was obtained."""
    op = "read_page"
    progress: list[dict] = []
    try:
        doc = open_source(source, password)
    except ReaderError as exc:
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
    #
    # Through the reader, not through pdfplumber. This imported pdfplumber
    # directly, so read_page on an HTML file would have run pdfminer over
    # markup; each format now answers with the tables it actually has.
    tables = page_tables(doc, [page])

    progress.append(info(f"read page {page} of {doc.page_count}"))
    if loaded.is_scanned:
        progress.append(warn("this page has no text layer", f"run ocr(pages='{page}')"))

    result = {
        "page": page,
        "of": doc.page_count,
        # None rather than [0.0, 0.0] for a format with no geometry -- a
        # zero page size reads as a measurement, and there is none to make.
        "size": [round(loaded.width, 1), round(loaded.height, 1)] if loaded.width else None,
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
    except ReaderError as exc:
        return fail(op, str(exc), exc.hint, progress)
    except SelectionError as exc:
        return fail(op, str(exc), exc.hint, progress)

    from servers.docs_read._read_extract import _estimate_tokens, suggest_range

    estimated = _estimate_tokens(doc, wanted)
    ceiling = budget.max_response_tokens()
    if estimated > ceiling:
        # Measured, not scaled -- see suggest_range. The scaled version named a
        # range that its own estimator then refused.
        suggestion = format_pages(suggest_range(doc, wanted, ceiling, estimated))
        return refuse(
            op,
            f"{len(wanted)} page(s) is about {estimated:,} tokens, over the {ceiling:,} limit.",
            f"Use to_markdown(pages='{suggestion}'), or find() to locate what you need and extract() to read it.",
            limit=f"{ceiling} tokens",
            seen=f"~{estimated} tokens",
            progress=progress,
        )

    sized_by_page: dict[int, list[tuple[str, float]]] = {}
    # Tables the FORMAT declares are rendered as markdown HERE, before cleaning
    # rather than after. A `<table>`, a docx w:tbl and a worksheet all arrive as
    # one block that already carries its cells on Block.rows, and substituting
    # on the finished text does not work: clean.py normalises whitespace, so the
    # tab-separated flattening the block was keyed on no longer appears in it
    # and every table silently fell through to its flattened form.
    #
    # A PDF declares nothing, so for two rounds this function rendered no PDF
    # table at all and reported `tables: 0` for a page that is nothing but a
    # balance sheet -- while `extract_tables` and `read_page`, asked about the
    # same page in the same session, each returned one. Measured: 21 of 21
    # sampled pages of a bank filing. The control that shows the field means
    # what it says elsewhere is HTML and XLSX, where the two tools agree
    # exactly. A caller who converts a statement and reads `tables: 0`
    # concludes there is nothing to extract.
    #
    # `ruled` tables are spliced in below, because the file itself drew the
    # grid. `whitespace` ones are NOT: their shape is inferred from column
    # gaps at 0.5 confidence, and a markdown pipe table is an assertion that
    # the grid is real. They are counted and named instead, which is the same
    # distinction the rest of this module makes between a fact and a guess.
    reconstructed = _reconstructed_tables(doc, wanted)
    tables_rendered = 0
    tables_left_as_text = 0
    for number in wanted:
        loaded = load_page_words(doc, number)
        blocks = order.order_blocks(loaded, order.detect_columns(loaded))
        for entry in reconstructed.get(number, []):
            spliced = blocks if not _worth_rendering(entry) else _splice_table(blocks, entry)
            if spliced is blocks:
                tables_left_as_text += 1
            blocks = spliced
        declared_tables = {item.text.strip(): item.rows for item in blocks if item.kind == "table" and item.rows}
        rendered: list[tuple[str, float]] = []
        for line, size in order.lines_with_size(blocks):
            rows = declared_tables.get(line.strip())
            if rows:
                tables_rendered += 1
                rendered.append((_markdown_table(rows), size))
            else:
                rendered.append((line, size))
        sized_by_page[number] = rendered

    # Clean on the text alone, then re-attach sizes by position. Cleaning can
    # only remove whole lines (furniture) or join two into one (hyphens), so
    # matching cleaned text back to the size of the line it came from is a
    # lookup, not a guess -- and doing it this way keeps clean.py unaware that
    # font sizes exist.
    lines_by_page = {n: [text for text, _ in pairs] for n, pairs in sized_by_page.items()}
    text, cleaned = clean.clean_pages(lines_by_page)
    size_of = {t.strip(): sz for pairs in sized_by_page.values() for t, sz in pairs if t.strip()}

    # Headings the document DECLARES, from the reader that owns the level. HTML
    # says <h2>, a docx says a Heading 2 style, a deck says "this is the title
    # placeholder" -- none of which is a guess about type size. Only `native`
    # entries: a PDF's bookmarks are `tagged` and point at pages rather than
    # naming a line, so matching them against line text would be nonsense.
    declared = {
        str(entry["title"]).strip(): int(entry.get("level", 1))
        for entry in bookmarks(doc)
        if entry.get("basis") == "native" and str(entry.get("title", "")).strip()
    }
    body, used_declared = _mark_headings(text, size_of, declared)

    progress.append(info(f"converted {len(wanted)} page(s)"))
    result = {
        "markdown": body,
        "pages": format_pages(wanted),
        "characters": len(body),
        "headings": body.count("\n#") + body.startswith("#"),
        "tables": tables_rendered,
        "cleaned": cleaned,
    }
    # Only when there is something to disclose, and never as a bare zero --
    # `tables_left_as_text: 0` on a page with no tables at all would read as a
    # different claim from the same silence.
    # A note, and deliberately NOT a second count. The first version of this
    # reported `tables_left_as_text`, and measuring it killed it: pdfplumber's
    # text strategy proposes a grid for essentially EVERY page, so a six-page
    # prose document came back claiming six tables. Worse, the proposal cannot
    # be filtered by shape -- on this corpus a page of plain prose scores 100%
    # of rows holding two or more cells and a real consolidated balance sheet
    # scores 81%, so any threshold rates the prose as the more table-like of
    # the two. There is no honest count of "tables present" in a PDF, which is
    # the whole reason `basis` and `confidence` exist. Fixing an under-claim
    # with an over-claim is not a fix.
    if tables_left_as_text:
        result["tables_note"] = (
            "This format has no tables of its own; they are reconstructed, so `tables` counts the ones "
            "rendered here -- those the file rules a grid around. Others were left as text rather than "
            f"asserted as a grid. extract_tables(pages='{format_pages(wanted)}') returns every "
            "reconstruction with its basis and confidence."
        )
    # `native` where the document declared its own structure, `font_size` where
    # the headings were inferred from type. Reporting the second for the first
    # would throw away the difference between a fact and a guess -- outline()
    # makes the same distinction on the same documents, and the two tools
    # disagreeing about one file is worse than either being imprecise.
    if used_declared:
        return ok(op, result, progress, basis="native")
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


def _mark_headings(text: str, size_of: dict[str, float], declared: dict[str, int] | None = None) -> tuple[str, bool]:
    """Mark headings, preferring the level the document declares over type size.

    Returns the marked text and whether any DECLARED level was used, which is
    what decides the response's basis: `native` for a level the format stated,
    `font_size` for one inferred from type.

    Font size is a guess -- `font_size` is the IR's own name for it and one of
    the two lowest-confidence bases there is. It is the only signal a PDF
    offers, and the wrong one to use on a format that says <h2> outright.

    The inference is deliberately conservative in both directions. The
    threshold is a ratio against the MEDIAN line size, so a document set
    entirely in 18pt has no headings rather than all of them; and a line is
    only upgraded if it is short, because a full paragraph in a large face is a
    pull quote, not a heading. Over-marking turns a document into an outline of
    nonsense, while a missed heading is still readable prose.
    """
    declared = declared or {}
    sizes = [size for size in size_of.values() if size]
    if not sizes and not declared:
        return text, False
    body_size = sorted(sizes)[len(sizes) // 2] if sizes else 0.0
    threshold = body_size * HEADING_RATIO

    used_declared = False
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        level = declared.get(stripped)
        if stripped and level:
            used_declared = True
            # Clamped to 6: markdown has no seventh level, and `####### x`
            # renders as literal hashes rather than as the heading it meant.
            out.append(f"{'#' * min(level, 6)} {stripped}")
            continue
        size = size_of.get(stripped, 0.0)
        if stripped and size > threshold and len(stripped) <= MAX_HEADING_CHARS:
            level = 1 if size >= body_size * 1.5 else 2
            out.append(f"{'#' * level} {stripped}")
        else:
            out.append(line)
    return "\n".join(out), used_declared


def _reconstructed_tables(doc, wanted: list[int]) -> dict[int, list[dict]]:
    """Tables this page's FORMAT does not declare, grouped by page.

    Only the ones a reader had to reconstruct. A format that declares its
    tables already carries them as blocks with `rows`, and counting those here
    would report every HTML table twice -- once as rendered and once as
    outstanding.
    """
    out: dict[int, list[dict]] = {}
    for entry in page_tables(doc, wanted):
        if entry.get("basis") in {"ruled", "whitespace"} and entry.get("rows"):
            out.setdefault(int(entry["page"]), []).append(entry)
    return out


def _worth_rendering(entry: dict) -> bool:
    """Whether this reconstruction should become a markdown table.

    Two conditions, and both were learned by rendering the result and reading
    it rather than by reasoning about the code.

    `ruled` only. A `whitespace` shape is inferred from column gaps at 0.5
    confidence, and a pipe table is an assertion that the grid is real -- the
    one thing this module refuses to do everywhere else.

    And a real grid, not a frame. pdfplumber's line strategy calls the two
    ruled boxes on a filing's COVER page a 2x1 table, and rendering that turned
    a perfectly readable title page into a one-column markdown table holding
    two paragraphs. A single row or a single column carries no structure that
    plain text does not already carry, so rendering it only costs the line
    breaks.
    """
    rows = entry.get("rows") or []
    return entry.get("basis") == "ruled" and len(rows) >= 2 and max((len(r) for r in rows), default=0) >= 2


def _splice_table(blocks: list, entry: dict) -> list:
    """Replace the word blocks a table covers with the table itself.

    The blocks a PDF page yields are words at coordinates; a table found in it
    is a grid over the same words. Rendering both would print every cell twice,
    once inside the table and once as loose text around it, so the words inside
    the table's bounding box come out and one `table` block goes in where the
    first of them was -- which keeps the reading order that `order_blocks` just
    worked out, rather than appending the table at the end of the page.

    The synthetic block is a real `Block` with `rows`, so everything downstream
    treats it exactly like a docx `w:tbl`. No second mechanism.
    """
    from core.ir import Block, Span

    box = entry.get("bbox")
    if not box:
        return blocks
    x0, top, x1, bottom = box
    kept: list = []
    first_inside: int | None = None
    for block in blocks:
        bbox = block.bbox
        if bbox and x0 <= (bbox[0] + bbox[2]) / 2 <= x1 and top <= (bbox[1] + bbox[3]) / 2 <= bottom:
            if first_inside is None:
                first_inside = len(kept)
            continue
        kept.append(block)
    if first_inside is None:
        return blocks
    rows = entry["rows"]
    flat = " ".join(c for row in rows for c in row if c)
    kept.insert(
        first_inside,
        Block(kind="table", spans=[Span(text=flat)], bbox=(x0, top, x1, bottom), rows=rows, basis="ruled"),
    )
    return kept


def _markdown_table(rows: list[list[str]]) -> str:
    """A GitHub-flavoured table. Ragged rows are padded, never truncated.

    A row with fewer cells than the header is a real thing in real documents
    (a merged cell, a spanning total) and dropping the row or the extra cells
    would lose content to make the shape tidy.
    """
    width = max((len(row) for row in rows), default=0)
    if not width:
        return ""

    def cell(value: str) -> str:
        # A literal pipe would end the column, and a newline would end the
        # table -- both silently, producing a table that renders as fewer
        # columns than the document has.
        return value.replace("|", r"\|").replace("\n", " ").strip()

    padded = [[cell(c) for c in row] + [""] * (width - len(row)) for row in rows]
    header, *body_rows = padded
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in body_rows)
    return "\n".join(lines)
