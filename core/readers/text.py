"""Plain text, Markdown, CSV and logs -> core.ir.Document.

The simplest reader, and the one that makes `probe` useful on the files an
agent actually has lying around: a 40 MB log, a CSV someone dropped in, a
README. All of them are flow formats with no pages, so they are paginated the
same way HTML is and disclose it the same way.

Markdown is read as Markdown, not as plain text: `#` headings are real
structure the file declares, so they become `heading` blocks at `native` basis
and `outline` works on a README. What is deliberately NOT done is rendering --
no Markdown-to-HTML conversion, no library. `to_markdown` on a Markdown file
should return the file, and a round trip through a renderer would return
something subtly different from what the caller already had.

CSV gets one block per row and rows on the block, so `extract_tables` reads a
CSV as a table -- which is what it is, and what a caller asking a document
server about a CSV means.
"""

from __future__ import annotations

from core.ir import Block, Document, Span
from core.readers import _flow

# A markdown ATX heading: one to six hashes, a space, then the text. The
# closing hashes some writers add are stripped. Setext headings (underlined
# with === or ---) are handled separately because they need the NEXT line.
ATX = "#"
MAX_HEADING_LEVEL = 6

# Same mapping as the HTML reader's, and deliberately the same numbers: a
# level-2 heading in Markdown and an <h2> are the same thing, and two readers
# disagreeing about that would make `outline` answer differently for a file and
# its own HTML rendering.
HEADING_SIZES = {1: 26.0, 2: 22.0, 3: 18.0, 4: 16.0, 5: 14.0, 6: 13.0}
BODY_SIZE = 12.0

MARKDOWN_SUFFIXES = {".md", ".markdown"}
CSV_SUFFIXES = {".csv"}


def open_document(path: str, password: str = "") -> Document:
    """Read a text file whole. Refuses one too large to hold in memory."""
    src = _flow.guard_size(path)
    text = _flow.decode(src)
    suffix = src.suffix.lower()

    if suffix in CSV_SUFFIXES:
        blocks, meta, fmt = _csv_blocks(text)
    elif suffix in MARKDOWN_SUFFIXES:
        blocks, meta, fmt = _markdown_blocks(text)
    else:
        blocks, meta, fmt = _plain_blocks(text)

    meta["characters"] = len(text)
    # splitlines(), not count("\n"). A real file in the corpus
    # (US_Economic_News.csv) uses classic-Mac \r endings exclusively: 8,000 of
    # them and not one \n, so counting newlines reported "lines: 1" for a 12 MB
    # file the CSV reader had just read as 8,001 rows -- two fields of one
    # response contradicting each other. splitlines() handles \n, \r and \r\n.
    meta["lines"] = len(text.splitlines())
    return _flow.build(str(src), fmt, blocks, meta)


def _plain_blocks(text: str) -> tuple[list[Block], dict, str]:
    """One block per paragraph, a paragraph being a run of non-blank lines.

    Blank-line separated rather than one block per line: a block is the unit
    reading order and cleaning work on, and one block per line would make every
    line its own paragraph in `extract`. A log file with no blank lines at all
    becomes one block per page after `_split_oversized`, which is right -- there
    is no paragraph structure in it to find.
    """
    blocks: list[Block] = []
    buffer: list[str] = []
    for line in text.splitlines():
        if line.strip():
            buffer.append(line)
        elif buffer:
            blocks.append(_flow.block("\n".join(buffer), "para", "native", BODY_SIZE))
            buffer = []
    if buffer:
        blocks.append(_flow.block("\n".join(buffer), "para", "native", BODY_SIZE))
    return blocks, {}, "txt"


def _markdown_blocks(text: str) -> tuple[list[Block], dict, str]:
    """Paragraphs, plus the headings the file declares.

    Fenced code blocks are passed through as paragraphs without being scanned
    for headings: a `#` at the start of a line inside a shell script is a
    comment, and reading it as a heading puts shell comments in the outline.
    """
    blocks: list[Block] = []
    buffer: list[str] = []
    fenced = False

    def flush() -> None:
        nonlocal buffer
        if buffer:
            blocks.append(_flow.block("\n".join(buffer), "para", "native", BODY_SIZE))
            buffer = []

    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
            buffer.append(line)
            continue
        if fenced:
            buffer.append(line)
            continue

        level = _atx_level(stripped)
        if level:
            flush()
            title = stripped[level:].strip().rstrip(ATX).strip()
            blocks.append(_flow.block(title, "heading", "native", HEADING_SIZES[level]))
            continue

        # Setext: a line of text underlined by === (level 1) or --- (level 2).
        # Checked against the NEXT line, and only when this one has text, so a
        # horizontal rule (--- on its own) is not read as underlining a blank.
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if stripped and following and set(following) in ({"="}, {"-"}) and len(following) >= 3:
            flush()
            level = 1 if following[0] == "=" else 2
            blocks.append(_flow.block(stripped, "heading", "native", HEADING_SIZES[level]))
            continue
        if stripped and set(stripped) in ({"="}, {"-"}) and len(stripped) >= 3 and blocks:
            # The underline itself, already consumed by the branch above.
            continue

        if stripped:
            buffer.append(line)
        else:
            flush()
    flush()

    headings = sum(1 for b in blocks if b.kind == "heading")
    return blocks, {"headings": headings}, "md"


def _atx_level(stripped: str) -> int:
    """1-6 for `# Heading`, 0 for anything else.

    The space after the hashes is required, which is what tells `# Heading`
    from `#hashtag` and from a `#!/bin/sh` line at the top of a script.
    """
    if not stripped.startswith(ATX):
        return 0
    level = len(stripped) - len(stripped.lstrip(ATX))
    if level > MAX_HEADING_LEVEL or level >= len(stripped) or stripped[level] != " ":
        return 0
    return level


def _csv_blocks(text: str) -> tuple[list[Block], dict, str]:
    """The whole file as one table block, split into page-sized pieces later.

    Parsed with the csv module and its dialect sniffer rather than by splitting
    on commas: a quoted field containing a comma or a newline is the ordinary
    case in real exports, and splitting on the delimiter turns one row into
    several with the columns shifted -- data that is wrong rather than missing.
    """
    import csv
    import io

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        # An empty file, a single column, or one line -- all of which have no
        # delimiter to find. Comma is the right default and the response says
        # what was used.
        dialect = csv.excel

    # newline="" is REQUIRED, not tidiness. StringIO's default translates
    # newlines, and a quoted field containing a line break then reaches the
    # reader as a bare newline it refuses:
    #
    #     _csv.Error: new-line character seen in unquoted field
    #
    # Found on US_Economic_News.csv in the corpus -- a real export whose
    # article text spans lines inside its quotes. The exception escaped probe()
    # entirely, so a tool that owes every caller a dict raised instead. With
    # newline="" the same file reads as 8,001 rows.
    try:
        rows = [row for row in csv.reader(io.StringIO(text, newline=""), dialect) if any(c.strip() for c in row)]
    except csv.Error as exc:
        # Still not parseable as CSV -- a field over the size limit, or a file
        # that is not really delimited. It is a text file either way, and its
        # content is what the caller asked for, so fall back and SAY SO rather
        # than refusing a readable file over its extension.
        blocks, meta, _ = _plain_blocks(text)
        meta["csv_parse"] = f"failed ({exc}); read as plain text"
        return blocks, meta, "txt"

    if not rows:
        return [], {"rows": 0}, "csv"

    flat = "\n".join("\t".join(cell for cell in row) for row in rows)
    block = Block(kind="table", spans=[Span(text=flat, size=BODY_SIZE)], bbox=None, basis="native", rows=rows)
    meta = {
        "rows": len(rows),
        "columns": max(len(r) for r in rows),
        "delimiter": getattr(dialect, "delimiter", ","),
    }
    return [block], meta, "csv"


def page_tables(doc: Document, numbers: list[int]) -> list[dict]:
    """A CSV's rows, per page. Other text formats have no tables."""
    found: list[dict] = []
    for number in numbers:
        page = doc.pages.get(number)
        if page is None:
            continue
        for block in page.blocks:
            if block.kind != "table" or not block.rows:
                continue
            found.append(
                {
                    "page": number,
                    "rows": block.rows,
                    "row_count": len(block.rows),
                    "column_count": max(len(r) for r in block.rows),
                    "basis": "native",
                    "confidence": 1.0,
                }
            )
    return found


def bookmarks(doc: Document) -> list[dict]:
    """Markdown's own headings. Empty for plain text, which declares none."""
    levels = {size: level for level, size in HEADING_SIZES.items()}
    out: list[dict] = []
    for number in sorted(doc.pages):
        for block in doc.pages[number].blocks:
            if block.kind != "heading":
                continue
            size = block.spans[0].size if block.spans else BODY_SIZE
            out.append(
                {
                    "level": levels.get(size, MAX_HEADING_LEVEL),
                    "title": block.text.strip(),
                    "page": number,
                    "basis": "native",
                }
            )
    return out


def probe_extras(doc: Document) -> dict:
    keys = ("characters", "lines", "rows", "columns", "delimiter", "headings", "csv_parse")
    return {key: doc.meta[key] for key in keys if key in doc.meta}


close_document = _flow.close_document
load_page = _flow.load_page
load_page_words = _flow.load_page
