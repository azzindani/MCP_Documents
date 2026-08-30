"""Shared machinery for formats that have no pages.

HTML, Markdown, plain text, email and Word documents are a *flow*: text runs
continuously and where one page ends is a property of how you print it, not of
the file. PDF, slides, spreadsheets and epub chapters are the opposite -- the
file itself says where the boundaries are.

Every tool here counts in pages: `find` reports which page a match is on,
`extract` takes a page range, `probe` reports pages_that_fit. So a flow format
needs pages, and this builds them.

**A synthetic page is disclosed, never disguised.** Every flow reader sets
`meta["pagination"] = "synthetic"` and `meta["chars_per_page"]`, and `probe`
reports both. A caller who is told "page 7 of 12" for an HTML file and then
finds no such division in the source has been misled about the one thing this
server is supposed to be careful with -- where an answer came from.

**Blocks are never split across a page boundary.** A paragraph broken in half
by an arbitrary character count would break `find`'s snippets, defeat
de-hyphenation, and put half a sentence in each of two responses. Instead a
block LARGER than a page is split first, at line ends, into blocks that fit --
so the split lands somewhere the text already breaks.
"""

from __future__ import annotations

from pathlib import Path

from core import budget
from core.ir import Basis, Block, BlockKind, Document, Page


def guard_size(path: str) -> Path:
    """Refuse a file too large to parse whole, before opening it.

    Deliberately by stat rather than by reading: the point is to answer without
    paying the cost being refused.
    """
    from core.readers import ReaderError

    src = Path(path)
    if not src.exists():
        raise ReaderError(
            f"No file at {path!r}.",
            "Check the path, or pass a URL if MCP_FETCH_URLS=1 is set.",
        )
    size = src.stat().st_size
    ceiling = budget.max_source_bytes()
    if size > ceiling:
        raise ReaderError(
            f"{src.name} is {size / 1_048_576:.1f} MB, over the {ceiling / 1_048_576:.0f} MB limit "
            f"for a format that must be parsed in one pass.",
            "Split the file, or convert(to='pdf') first -- PDF is read page by page and has no such limit.",
        )
    return src


def decode(src: Path) -> str:
    """Bytes to text, guessing the encoding only when it is not declared.

    charset-normalizer rather than chardet: it is the one already in the
    dependency set (requests pulls it), it is permissively licensed, and it is
    markedly better on the case that actually turns up here -- a Windows-1252
    page that declares itself UTF-8 and has one smart quote in it.
    """
    raw = src.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    from charset_normalizer import from_bytes

    best = from_bytes(raw).best()
    if best is None:
        # Never raise here. A file with no coherent encoding still has content
        # a caller may want, and replacement characters are a visible, honest
        # failure -- unlike an exception, which loses the whole document over
        # one bad byte.
        return raw.decode("utf-8", errors="replace")
    return str(best)


def block(text: str, kind: BlockKind = "para", basis: Basis = "native", size: float = 0.0) -> Block:
    """One block with no geometry.

    bbox stays None all the way through: `core.order` reads that as "keep the
    reader's order, it IS the reading order", and every layout-dependent tool
    checks for it rather than reading a zero as a real coordinate.
    """
    from core.ir import Span

    return Block(kind=kind, spans=[Span(text=text, size=size)], bbox=None, basis=basis)


def _split_table(item: Block, limit: int) -> list[Block]:
    """Break an oversized table by ROWS, so each piece is still a table.

    Splitting a table's flattened text at a character count would leave a
    fragment ending mid-cell and a `rows` list that no longer matches the text
    beside it -- two fields describing one table in contradictory terms. A
    10,000-row HTML table is an ordinary thing to be handed, so this has to
    work rather than refuse.

    The header row is NOT repeated on each piece. Copying it would make every
    fragment look like a complete table and put a row in the response that is
    not at that position in the source; `page_tables` reports each fragment's
    real rows and the caller can see the first one carries the header.
    """
    from core.ir import Span

    rows = item.rows or []
    pieces: list[Block] = []
    current: list[list[str]] = []
    size = 0
    for row in rows:
        length = sum(len(cell) + 1 for cell in row)
        if current and size + length > limit:
            pieces.append(_table_piece(current, Span))
            current, size = [], 0
        current.append(row)
        size += length
    if current:
        pieces.append(_table_piece(current, Span))
    return pieces or [item]


def _table_piece(rows: list[list[str]], span_cls) -> Block:
    flat = "\n".join("\t".join(cell for cell in row) for row in rows)
    return Block(kind="table", spans=[span_cls(text=flat)], bbox=None, basis="native", rows=rows)


def _split_oversized(blocks: list[Block], limit: int) -> list[Block]:
    """Break any block bigger than one page, at line ends where possible."""
    out: list[Block] = []
    for item in blocks:
        text = item.text
        if len(text) <= limit:
            out.append(item)
            continue
        if item.rows:
            out.extend(_split_table(item, limit))
            continue
        lines = text.splitlines(keepends=True) or [text]
        buffer = ""
        for line in lines:
            # A single line longer than a page (minified HTML, one long CSV
            # row) has no line end to split at, so it is cut at the limit. That
            # is the one place a block is broken mid-text, and it happens only
            # where the source offered nowhere better.
            while len(line) > limit:
                if buffer:
                    out.append(block(buffer, item.kind, item.basis))
                    buffer = ""
                out.append(block(line[:limit], item.kind, item.basis))
                line = line[limit:]
            if len(buffer) + len(line) > limit and buffer:
                out.append(block(buffer, item.kind, item.basis))
                buffer = ""
            buffer += line
        if buffer:
            out.append(block(buffer, item.kind, item.basis))
    return out


def paginate(blocks: list[Block], basis: Basis = "native") -> list[Page]:
    """Pack blocks into synthetic pages of roughly one real page each.

    Always returns at least one page: an empty document is one empty page, not
    zero pages. A document reporting `pages: 0` reads as a failed open, and
    every page-range check downstream would then refuse a valid request for
    page 1.
    """
    limit = budget.flow_chars_per_page()
    blocks = _split_oversized(blocks, limit)

    pages: list[Page] = []
    current: list[Block] = []
    size = 0

    def flush() -> None:
        number = len(pages) + 1
        page = Page(number=number, width=0.0, height=0.0, basis=basis if current else "empty")
        for index, item in enumerate(current):
            item.order = index
        page.blocks = list(current)
        pages.append(page)

    for item in blocks:
        length = len(item.text)
        if current and size + length > limit:
            flush()
            current, size = [], 0
        current.append(item)
        size += length
    if current or not pages:
        flush()
    return pages


def build(source: str, fmt: str, blocks: list[Block], meta: dict | None = None, basis: Basis = "native") -> Document:
    """A finished Document from a flat list of blocks, paginated and disclosed."""
    pages = paginate(blocks, basis)
    doc = Document(
        source=source,
        format=fmt,
        page_count=len(pages),
        pages={p.number: p for p in pages},
        meta={
            "bytes": Path(source).stat().st_size if Path(source).exists() else 0,
            # The disclosure. Read by probe() and reported to the caller.
            "pagination": "synthetic",
            "chars_per_page": budget.flow_chars_per_page(),
            **(meta or {}),
        },
    )
    return doc


def paged(source: str, fmt: str, pages: list[Page], meta: dict | None = None) -> Document:
    """A finished Document for a format whose pages are REAL.

    Slides, worksheets and epub spine items are genuine boundaries the file
    itself declares, so these carry no `pagination: synthetic` disclosure --
    saying a slide deck's pages are synthetic would be as wrong as the reverse.
    """
    for index, page in enumerate(pages, start=1):
        page.number = index
    return Document(
        source=source,
        format=fmt,
        page_count=len(pages),
        pages={p.number: p for p in pages},
        meta={
            "bytes": Path(source).stat().st_size if Path(source).exists() else 0,
            "pagination": "native",
            **(meta or {}),
        },
    )


def close_document(doc: Document) -> None:
    """Flow readers hold no handle: everything was read at open.

    Defined here and re-exported by each of them so the router's contract is
    satisfied uniformly, rather than every reader carrying the same three
    lines.
    """
    doc.handle = None
    doc.password = ""


def load_page(doc: Document, number: int) -> Page:
    """Pages of a flow document are built at open, so this is a lookup.

    Not lazy, and it cannot be: a flow parser builds the whole tree or nothing.
    That is why `guard_size` refuses a large one at the door instead.
    """
    from core.readers import ReaderError

    page = doc.pages.get(number)
    if page is None:
        raise ReaderError(
            f"Page {number} is out of range; this document has {doc.page_count}.",
            f"Use a page between 1 and {doc.page_count}, or probe() to see the count.",
        )
    return page
