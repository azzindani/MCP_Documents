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
from typing import Any, Literal, get_args

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

    # Cell rows, for a `table` block in a format that DECLARES its tables --
    # an HTML <table>, a docx w:tbl, a worksheet. None for a PDF, whose tables
    # are not declared but reconstructed from ruling lines or column gaps by
    # core/tables.py, which reports a confidence alongside them.
    #
    # A real field rather than a side table keyed on id(block): CPython reuses
    # an id as soon as the object is collected, so a lookup keyed that way
    # silently returns another block's rows once the first is freed.
    rows: list[list[str]] | None = None

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

    # Is there anything on this page that OCR could read? Set by a reader that
    # can see ink -- the PDF reader counts the page's drawable objects. Without
    # it, a page with no text layer is ambiguous between "a scan" and "a blank
    # separator sheet", and probe() reported the two as one bucket: a 1 MB
    # scanned invoice came back as `empty: 1, scanned: 0`.
    #
    # Defaults to FALSE, so a format with no pixels (HTML, text, email) never
    # claims a blank page could be OCR'd. Only a reader that has actually
    # looked sets it True.
    has_content: bool = False

    # The page's text AS THE READER READ IT, before this module turned it into
    # blocks. Set by the reader; never rebuilt from `blocks`.
    #
    # It exists because `text` below is not a property of the page, it is a
    # property of whichever reader last touched it. `load_page` builds one block
    # per LINE, so joining with "\n" reproduces the page. `load_page_words`
    # builds one block per WORD and REPLACES the cached page, so the same join
    # yields one word per line and every space in the document becomes a
    # newline. `find` searched that, and a two-word query stopped matching on
    # any page another tool had already read:
    #
    #     find('JUMLAH EKUITAS')                      -> 5 hits
    #     extract(pages='7'); find('JUMLAH EKUITAS')  -> 3 hits
    #
    # Same document, same call, fewer answers, no warning -- and the pages that
    # vanish are the ones the caller just looked at, on the documented
    # probe -> find -> extract path.
    raw_text: str = ""

    @property
    def text(self) -> str:
        """The page's text. Prefers what the reader read over what was built.

        Falls back to the blocks for readers with no separate text layer of
        their own, where the blocks ARE the reading.
        """
        return self.raw_text or "\n".join(b.text for b in self.blocks)

    @property
    def char_count(self) -> int:
        """How much text this page holds, counted the way `text` reads it.

        From `text`, NOT from the blocks -- for exactly the reason `text` above
        and `is_scanned` below are both written the way they are. Summing the
        spans counts whichever blocks happen to be cached, and
        `load_page_words` REPLACES a page's line blocks with one block per
        word, dropping every space between them:

            probe()                        -> 171,634 tokens, 8 pages fit
            extract(pages='6-7'); probe()  -> 171,516 tokens
            (all 183 pages read); probe()  -> 149,410 tokens, 9 pages fit

        Same document, same call. `token_estimate_full` is the number whose
        whole job is telling a caller why they must not ask for the document at
        once, and it shrank by 12.9% as they read it -- downward, which is the
        dangerous direction, and far enough to move
        `pages_that_fit_one_response` from 8 to 9, so the range probe suggests
        overflows the response it was sized for.

        Round 2 introduced `raw_text` and routed `text` and `is_scanned`
        through it. This property sits between those two and was left counting
        blocks.
        """
        return len(self.text)

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


_BASES: frozenset[str] = frozenset(get_args(Basis))


def as_basis(value: str, fallback: Basis = "text_layer") -> Basis:
    """Narrow a runtime string to a Basis, falling back rather than raising.

    A real check, not a cast to quiet the type checker: `basis` values arrive
    from reader modules and travel into responses, and a reader with a typo
    would otherwise put an unknown word in a field whose whole purpose is that
    a caller can rely on its vocabulary. The fallback is the conservative
    answer, never the confident one.
    """
    return value if value in _BASES else fallback  # type: ignore[return-value]


# Weakest claim to strongest. `empty` is absent deliberately: it is not a weak
# basis, it is the absence of content, and every caller of weakest_basis()
# decides separately whether there was anything at all.
_BASIS_STRENGTH: tuple[str, ...] = (
    "whitespace",  # inferred from column gaps       -- a guess about structure
    "font_size",  # heading guessed from typography -- a guess about intent
    "ocr",  # recognised from pixels
    "text_layer",  # glyphs the file contains
    "ruled",  # a grid the file draws
    "tagged",  # the file's own structure tree
    "native",  # the file declares this outright
)


def weakest_basis(values, fallback: Basis = "text_layer") -> Basis:
    """The weakest basis among several, for a response that summarises many.

    A response covering a page range, a set of tables or a whole document makes
    ONE claim about all of it, so it has to make the claim that is true of the
    worst piece. Reporting the strongest would tell a caller that a table
    inferred from column gaps is as reliable as one the file declared.

    Three tools each decided this for themselves and each got it wrong in a
    different way. `extract` and `find` returned the constant `"text_layer"`,
    which is a PDF's answer and wrong for the twelve formats that declare their
    structure. `extract_tables` knew only two values -- it returned `"ruled"`
    when every table was ruled and `"whitespace"` otherwise -- so a `native`
    table from an HTML file, a worksheet or a set of tagged XBRL facts was
    summarised as `whitespace`: the lowest confidence in the vocabulary,
    attached to the one kind of table that involves no inference at all.
    """
    known = [v for v in values if v in _BASIS_STRENGTH]
    if not known:
        return fallback
    return min(known, key=_BASIS_STRENGTH.index)  # type: ignore[return-value]


def pages_of_kind(doc: Document, scanned: bool) -> list[int]:
    """Page numbers that are (or are not) scans, among the pages loaded."""
    return [p.number for p in doc.loaded_pages if p.is_scanned is scanned]
