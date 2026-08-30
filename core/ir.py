"""The one document model every reader produces and every tool consumes.

This is the decision that keeps the tool count at 13. A reader's whole job is
to turn its format into `Document`; no tool below this layer ever asks what it
was handed. Adding `.epub` support costs one reader, not five tools.

Coordinates are PDF-style points (1/72 inch) with the origin at the TOP-LEFT
and y increasing downward, because that is what every consumer here wants --
"which of these two blocks is higher on the page" should be `a.y0 < b.y0`
rather than a comparison whose direction depends on the source format. Readers
that get bottom-left coordinates from their library convert once, on the way
in; readers with no geometry at all (plain text, an email body) pass `bbox=None`
and the layout-dependent tools say so rather than inventing numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# How a piece of content was obtained. This travels with the content all the
# way to the response, because a PDF is glyphs at coordinates -- paragraphs,
# tables, reading order and headings are all RECONSTRUCTED, and the caller has
# no way to tell a certainty from a guess unless we say.
Basis = Literal[
    "text_layer",  # glyphs the file actually contains          -- high
    "tagged",  # the PDF's own structure tree (PDF/UA)      -- high
    "ruled",  # table found from ruling lines              -- high
    "native",  # a format that really has this structure    -- high (docx, html)
    "ocr",  # recognised from pixels; carries confidence -- medium
    "whitespace",  # table inferred from column gaps            -- LOW, and says so
    "font_size",  # heading guessed from typography            -- LOW
    "empty",  # nothing here to obtain
]

BlockKind = Literal["para", "heading", "table", "figure", "caption", "list", "head_foot"]

# A page with fewer than this many extractable characters is treated as having
# no text layer. Not zero: a scanned page routinely carries a handful of stray
# glyphs from a stamp, a page number burned in by the scanner, or an OCR layer
# someone added to page 1 only. Zero would classify those as born-digital and
# send the caller looking for text that is not there.
MIN_CHARS_FOR_TEXT_LAYER = 32


@dataclass(slots=True)
class Span:
    """A run of text with one set of visual attributes."""

    text: str
    bbox: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1
    font: str = ""
    size: float = 0.0
    bold: bool = False
    italic: bool = False

    @property
    def x0(self) -> float:
        return self.bbox[0] if self.bbox else 0.0

    @property
    def y0(self) -> float:
        return self.bbox[1] if self.bbox else 0.0


@dataclass(slots=True)
class Block:
    """A contiguous piece of a page: a paragraph, a heading, a table cell run."""

    kind: BlockKind
    spans: list[Span] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None
    order: int = 0  # reading-order index within the page, set by core.order
    column: int = 0  # 0-based column index, set by core.order
    basis: Basis = "text_layer"

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass(slots=True)
class Page:
    """One page. `basis` and `confidence` are per page, never per document.

    A hybrid PDF is born-digital on page 1 and a scan on page 40. One figure
    for the document would be a lie about both, which is why `probe` reports
    `page_kinds` and a `scanned_pages` selection string rather than a single
    verdict.
    """

    number: int  # 1-based, as every user-facing page number is
    width: float = 0.0
    height: float = 0.0
    rotation: int = 0
    blocks: list[Block] = field(default_factory=list)
    basis: Basis = "text_layer"
    confidence: float | None = None  # only where the method has one (OCR)
    columns: int = 1  # detected, never assumed -- see core/order.py

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)

    @property
    def char_count(self) -> int:
        return sum(len(s.text) for b in self.blocks for s in b.spans)

    @property
    def is_scanned(self) -> bool:
        """No usable text layer, so the pixels are the only content.

        Reads the reader's verdict rather than recounting. Counting here meant
        counting whatever blocks happened to be present, and word blocks carry
        no spaces while line blocks do -- so one page answered differently
        depending on which reader had most recently touched it.
        """
        return self.basis in {"empty", "whitespace"} and self.basis != "ocr"


@dataclass(slots=True)
class Document:
    """A whole document, however it arrived.

    Pages are populated lazily by the reader: `page_count` is known after the
    cheap open, `pages` fills in as they are asked for. Nothing here may load a
    500-page file to answer a question about page 3.
    """

    source: str
    format: str  # pdf | html | docx | xlsx | pptx | eml | epub | md | txt
    page_count: int = 0
    pages: dict[int, Page] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)
    encrypted: bool = False
    tagged: bool = False

    # Reader-private state, as real typed fields rather than keys in `meta`.
    # Both lived in `meta: dict[str, object]` first, which type-checks as
    # `object` and needs a cast at every use -- four pyright errors, each of
    # which a cast would have silenced by assertion rather than fixed.
    #
    # Neither is ever serialised: tools build `result` field by field, and
    # tests assert that no response carries a heap address or the password.
    # `handle` is deliberately Any -- it is a library object (a PDFium
    # document, an lxml tree), and the IR is the layer that does not know
    # about libraries.
    handle: Any = None
    password: str = ""

    def page(self, number: int) -> Page | None:
        return self.pages.get(number)

    @property
    def loaded_pages(self) -> list[Page]:
        return [self.pages[n] for n in sorted(self.pages)]


def pages_of_kind(doc: Document, scanned: bool) -> list[int]:
    """Page numbers that are (or are not) scans, among the pages loaded."""
    return [p.number for p in doc.loaded_pages if p.is_scanned is scanned]
