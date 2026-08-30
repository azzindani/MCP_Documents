"""PDF -> core.ir.Document.

Two libraries, deliberately, on different jobs:

    pypdfium2   opening, page geometry, and the text layer. It is a C library
                and it is fast enough to read the text of every page of a
                500-page document while answering a question about page 3.
    pdfplumber  word boxes and ruling lines, which cost 10-100x more per page.
                Only loaded when a caller asks for something that needs
                geometry, and only for the pages asked about.

Neither is loaded eagerly. `open_document` does the cheap open -- page count,
encryption, sizes -- and pages fill in as they are requested, because nothing
here may parse 500 pages to answer a question about one.

Use `get_text_bounded()`, never `get_text_range()`. Called with default
arguments the latter emits

    UserWarning: get_text_range() call with default params will be implicitly
    redirected to get_text_bounded()

and warning text has a way of ending up in a response field, which this
server's own contract forbids. The two return identical text.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

from core.ir import Block, Document, Page, Span

# pdfplumber is imported lazily inside the functions that need geometry: it
# pulls pdfminer.six, which is pure Python and slow to import, and the common
# path (probe, find, extract on a born-digital file) never touches it.


class PdfError(Exception):
    """Raised with a hint the tool layer can hand straight to the caller."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


def open_document(path: str, password: str = "") -> Document:
    """Open a PDF cheaply: page count, encryption, per-page geometry.

    No text is read here. `probe` needs the text layer and asks for it; every
    edit tool needs only this.
    """
    src = Path(path)
    if not src.exists():
        raise PdfError(f"No file at {path!r}.", "Check the path, or pass a URL if MCP_FETCH_URLS=1 is set.")

    try:
        pdf = pdfium.PdfDocument(src, password=password or None)
    except pdfium.PdfiumError as exc:
        message = str(exc)
        if "password" in message.lower():
            raise PdfError(
                f"{src.name} is encrypted and the password was wrong or missing.",
                "Pass the password to probe(password=...), or use protect(action='decrypt') first.",
            ) from exc
        raise PdfError(
            f"{src.name} could not be opened: {message}",
            "The file may be truncated or damaged. Try optimize(action='repair') first.",
        ) from exc

    doc = Document(
        source=str(src),
        format="pdf",
        page_count=len(pdf),
        encrypted=bool(password),
        meta={"bytes": src.stat().st_size},
    )
    doc.handle = pdf
    doc.password = password
    return doc


def close_document(doc: Document) -> None:
    if doc.handle is not None:
        doc.handle.close()
    doc.handle = None
    doc.password = ""


def load_page(doc: Document, number: int) -> Page:
    """Read one page's geometry and text layer into the IR. 1-based.

    Cached on the Document, so asking twice costs once -- the intended
    probe -> find -> extract sequence would otherwise re-read every page it
    touches three times.
    """
    existing = doc.pages.get(number)
    if existing is not None:
        return existing

    pdf = doc.handle
    if pdf is None:
        raise PdfError("The document handle is closed.", "Call open_document() again before reading pages.")

    raw = pdf[number - 1]
    width, height = raw.get_size()
    page = Page(number=number, width=width, height=height, rotation=raw.get_rotation())

    text = raw.get_textpage().get_text_bounded()
    # One block per line at this stage. Paragraph grouping and reading order
    # are core/order.py's job and need geometry, which costs more than most
    # callers need -- so this layer stays honest and cheap.
    page.blocks = [
        Block(kind="para", spans=[Span(text=line)], order=i, basis="text_layer")
        for i, line in enumerate(text.splitlines())
        if line.strip()
    ]

    # `basis` is decided against MIN_CHARS_FOR_TEXT_LAYER, NOT against "is there
    # any text at all". The first version of this asked `if text.strip()` and
    # produced a page reporting basis="text_layer" beside is_scanned=True --
    # two fields describing one page in contradictory terms, which is the exact
    # defect class this repo is built to avoid.
    #
    # The hybrid fixture is what surfaced it: its page 4 is a scan carrying a
    # single burned-in page number, the way real scans do. One stray glyph is
    # not a text layer.
    page.basis = "text_layer" if not page.is_scanned else "empty"

    doc.pages[number] = page
    return page


def load_page_words(doc: Document, number: int) -> Page:
    """Re-read one page WITH word geometry, via pdfplumber.

    Separate from load_page because it is 10-100x more expensive per page, and
    the tools that need it (tables, reading order, redaction) name themselves.
    Replaces the cheap page in the cache, since a page with geometry is
    strictly more useful than one without.
    """
    import pdfplumber

    with pdfplumber.open(doc.source, password=doc.password) as plumbed:
        raw = plumbed.pages[number - 1]
        page = Page(
            number=number,
            width=float(raw.width),
            height=float(raw.height),
            rotation=int(raw.rotation or 0),
        )
        blocks = []
        for i, word in enumerate(raw.extract_words(use_text_flow=False, keep_blank_chars=False)):
            span = Span(
                text=str(word["text"]),
                bbox=(float(word["x0"]), float(word["top"]), float(word["x1"]), float(word["bottom"])),
                size=float(word.get("size") or 0.0),
            )
            blocks.append(Block(kind="para", spans=[span], bbox=span.bbox, order=i, basis="text_layer"))
        page.blocks = blocks
        page.basis = "text_layer" if blocks else "empty"

    doc.pages[number] = page
    return page


def ruled_pages(doc: Document, numbers: list[int]) -> set[int]:
    """Which of these pages carry the ruling lines that make a table `ruled`.

    Takes a LIST because opening pdfplumber is the expensive part, not reading
    a page out of it. The per-page version of this called `pdfplumber.open()`
    once per sampled page -- twenty full re-parses of the file to answer twenty
    cheap questions, and most of probe()'s time on a large document.

    pdfplumber reports a stroked rectangle under `rects`, not `lines`: a table
    drawn as one `re S` per cell has `lines == 0` and `rects == 15`, which
    reads as "no ruling lines" if you check only the obvious attribute.
    Measured on the corpus -- ruled_table.pdf is lines=0 rects=15 edges=60,
    unruled_table.pdf is 0/0/0.
    """
    import pdfplumber

    found: set[int] = set()
    with pdfplumber.open(doc.source, password=doc.password) as plumbed:
        for number in numbers:
            raw = plumbed.pages[number - 1]
            if raw.lines or raw.rects or raw.edges:
                found.add(number)
    return found
