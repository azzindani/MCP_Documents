"""Build the document corpus the tests need, from raw PDF primitives.

Fixtures come BEFORE the tools that read them (CLAUDE.md §14, Phase 2). A
synthetic one-page PDF exercises nothing that matters: every hard case in this
repo -- two-column reading order, unruled tables, running heads, hyphenation
across a page break, a hybrid of scanned and born-digital pages, the budget
refusals -- is invisible without a fixture designed to expose it.

The fleet's highest-yield technique is "build the input that separates a good
answer from a lucky one". A 95/5 imbalanced target once made a classifier report
95% accuracy and 0.926 f1, both arithmetically correct, having found zero of the
ten positives. An ordinary document will never tell you that your reading-order
code is wrong; a two-column one tells you immediately.

Written against raw PDF content streams with pikepdf rather than a PDF library,
for two reasons. Byte-level control is the point -- "a page with no text layer"
and "a page whose text is in two columns at these exact x positions" are
statements about the file, not about a rendering. And it adds no dependency: a
fixture builder that needs reportlab or LibreOffice is a fixture builder that
does not run in CI on three platforms.

    uv run python tests/fixtures/build.py

Output goes to tests/fixtures/corpus/, which is gitignored: the 500-page budget
fixture is large, and a corpus must never be committed to a public repo. THIS
FILE is the artifact; its output is not.
"""

from __future__ import annotations

import zlib
from pathlib import Path

import pikepdf

CORPUS = Path(__file__).parent / "corpus"

# US Letter in points (1/72 inch), the size every helper below assumes.
W, H = 612.0, 792.0


def _text_ops(items: list[tuple[float, float, str, float]]) -> bytes:
    """Content-stream operators drawing text at absolute positions.

    Each item is (x, y_from_top, text, size). PDF's own origin is bottom-left,
    so the flip happens here, once -- callers think in "distance down the page"
    the same way core/ir.py does.
    """
    parts = [b"BT"]
    for x, y_top, text, size in items:
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        parts.append(f"/F1 {size} Tf 1 0 0 1 {x:.2f} {H - y_top:.2f} Tm ({escaped}) Tj".encode("latin-1"))
    parts.append(b"ET")
    return b"\n".join(parts)


def _rect_ops(rects: list[tuple[float, float, float, float]], width: float = 0.7) -> bytes:
    """Stroked rectangles -- the ruling lines that make a table `ruled`."""
    parts = [f"{width} w".encode()]
    for x, y_top, w, h in rects:
        parts.append(f"{x:.2f} {H - y_top - h:.2f} {w:.2f} {h:.2f} re S".encode())
    return b"\n".join(parts)


def _page(pdf: pikepdf.Pdf, *streams: bytes) -> pikepdf.Page:
    """One page carrying the given content streams, with Helvetica bound.

    Returns a pikepdf.Page, not the raw Dictionary: PageList.append accepts
    only the former, and the error if you forget names the type rather than
    the fix.
    """
    content = b"\n".join(s for s in streams if s)
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )
    stream = pdf.make_stream(zlib.compress(content))
    stream.Filter = pikepdf.Name.FlateDecode
    return pikepdf.Page(
        pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Page,
                MediaBox=[0, 0, W, H],
                Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
                Contents=pdf.make_indirect(stream),
            )
        )
    )


def _save(pdf: pikepdf.Pdf, name: str) -> Path:
    CORPUS.mkdir(parents=True, exist_ok=True)
    path = CORPUS / name
    pdf.save(path)
    return path


# --------------------------------------------------------------------------
# The corpus. Each builder exists to make ONE thing fail loudly if it is wrong.
# --------------------------------------------------------------------------


def born_digital(pages: int = 3, name: str = "born_digital.pdf") -> Path:
    """The control. Everything should be easy here; if it is not, stop."""
    pdf = pikepdf.Pdf.new()
    for n in range(1, pages + 1):
        body = [(72.0, 100.0 + i * 16, f"Page {n} line {i + 1}: the quick brown fox.", 11.0) for i in range(30)]
        pdf.pages.append(_page(pdf, _text_ops(body)))
    return _save(pdf, name)


def two_column(pages: int = 2, name: str = "two_column.pdf") -> Path:
    """Reading order. Read straight across and every sentence interleaves.

    The two columns say different things on purpose: a left column reading
    "LEFT n" and a right reading "RIGHT n" makes a wrong reading order
    immediately legible as LEFT 1 RIGHT 1 LEFT 2 ... instead of LEFT 1 LEFT 2.
    """
    pdf = pikepdf.Pdf.new()
    for n in range(1, pages + 1):
        items: list[tuple[float, float, str, float]] = []
        for i in range(24):
            y = 110.0 + i * 18
            items.append((60.0, y, f"LEFT p{n} line {i + 1} lorem ipsum dolor", 10.0))
            items.append((330.0, y, f"RIGHT p{n} line {i + 1} sit amet consect", 10.0))
        pdf.pages.append(_page(pdf, _text_ops(items)))
    return _save(pdf, name)


def running_heads(pages: int = 6, name: str = "running_heads.pdf") -> Path:
    """The same header and footer at the same y on every page.

    Without stripping, one line in eight of the extracted text is furniture.
    """
    pdf = pikepdf.Pdf.new()
    for n in range(1, pages + 1):
        items = [
            (72.0, 48.0, "CONFIDENTIAL - Acme Corporation - Internal Use Only", 9.0),
            (72.0, 744.0, f"Acme Annual Report 2026    Page {n} of {pages}", 9.0),
        ]
        # Body text deliberately NOT templated on the page number. The first
        # version read "Body sentence {i} on page {n}", which after the
        # cleaner blanks digit runs has the identical shape on every page --
        # so the body was indistinguishable from a running footer, and a
        # fixture built to test header stripping instead proved that body text
        # could be stripped. A fixture must not share the property it exists
        # to isolate.
        words = [
            "quarterly",
            "revenue",
            "segment",
            "margin",
            "outlook",
            "supply",
            "hedging",
            "capital",
            "goodwill",
            "covenant",
            "dividend",
            "buyback",
        ]
        items += [
            (72.0, 120.0 + i * 16, f"The {words[(i + n) % len(words)]} position was reviewed by the board.", 11.0)
            for i in range(28)
        ]
        pdf.pages.append(_page(pdf, _text_ops(items)))
    return _save(pdf, name)


def hyphenated(name: str = "hyphenated.pdf") -> Path:
    """A word split by a hyphen at a line break, and again across a page break.

    The page-break case is the one a per-page cleaner gets wrong.
    """
    pdf = pikepdf.Pdf.new()
    first = [
        (72.0, 100.0, "The plant increased its manu-", 11.0),
        (72.0, 118.0, "facturing output substantially.", 11.0),
        (72.0, 136.0, "This is a normal hyphen-word: state-of-the-art.", 11.0),
        (72.0, 700.0, "The committee will recon-", 11.0),
    ]
    second = [(72.0, 100.0, "sider the proposal next quarter.", 11.0)]
    pdf.pages.append(_page(pdf, _text_ops(first)))
    pdf.pages.append(_page(pdf, _text_ops(second)))
    return _save(pdf, name)


def ruled_table(name: str = "ruled_table.pdf") -> Path:
    """A table with real ruling lines -- basis must come back "ruled"."""
    pdf = pikepdf.Pdf.new()
    rows, cols = 5, 3
    x0, y0, cw, rh = 72.0, 120.0, 140.0, 24.0
    rects = [(x0 + c * cw, y0 + r * rh, cw, rh) for r in range(rows) for c in range(cols)]
    header = ["Item", "Qty", "Price"]
    body = [["Widget", "4", "12.50"], ["Gadget", "11", "3.00"], ["Sprocket", "2", "44.25"], ["Flange", "7", "9.99"]]
    items = [(x0 + c * cw + 6, y0 + 17, header[c], 10.0) for c in range(cols)]
    for r, row in enumerate(body, start=1):
        items += [(x0 + c * cw + 6, y0 + r * rh + 17, row[c], 10.0) for c in range(cols)]
    pdf.pages.append(_page(pdf, _rect_ops(rects), _text_ops(items)))
    return _save(pdf, name)


def unruled_table(name: str = "unruled_table.pdf") -> Path:
    """The same table with no lines at all -- basis must come back "whitespace".

    The pair is the point: a tool that reports the same confidence for this and
    for ruled_table.pdf has not implemented provenance, it has implemented a
    constant.
    """
    pdf = pikepdf.Pdf.new()
    x0, y0, cw, rh = 72.0, 120.0, 140.0, 24.0
    header = ["Item", "Qty", "Price"]
    body = [["Widget", "4", "12.50"], ["Gadget", "11", "3.00"], ["Sprocket", "2", "44.25"], ["Flange", "7", "9.99"]]
    items = [(x0 + c * cw + 6, y0 + 17, header[c], 10.0) for c in range(3)]
    for r, row in enumerate(body, start=1):
        items += [(x0 + c * cw + 6, y0 + r * rh + 17, row[c], 10.0) for c in range(3)]
    pdf.pages.append(_page(pdf, _text_ops(items)))
    return _save(pdf, name)


def hybrid(name: str = "hybrid.pdf") -> Path:
    """Born-digital pages and pages with NO text layer, in one file.

    Pages 3 and 4 carry drawn rectangles only -- graphically non-empty, textually
    empty, which is exactly what a scan looks like to an extractor. This is the
    fixture that proves `basis` is per page: a single verdict for this document
    is a lie about half of it.

    Page 4 also carries three stray glyphs, the way a real scan carries a burned
    -in page number. A "has any text at all" check calls that page born-digital
    and sends the caller looking for text that is not there, which is why
    ir.MIN_CHARS_FOR_TEXT_LAYER is 32 and not 0.
    """
    pdf = pikepdf.Pdf.new()
    for n in (1, 2):
        body = [(72.0, 100.0 + i * 16, f"Digital page {n}, line {i + 1}.", 11.0) for i in range(30)]
        pdf.pages.append(_page(pdf, _text_ops(body)))
    boxes = [(100.0, 150.0, 400.0, 60.0), (100.0, 250.0, 400.0, 300.0)]
    pdf.pages.append(_page(pdf, _rect_ops(boxes, width=1.5)))
    pdf.pages.append(_page(pdf, _rect_ops(boxes, width=1.5), _text_ops([(300.0, 760.0, "4", 9.0)])))
    return _save(pdf, name)


def large(pages: int = 500, name: str = "large.pdf") -> Path:
    """The budget fixture. The budgets are untestable without it.

    Roughly 1,300 characters per page, so ~650k characters and ~160k tokens --
    comfortably past any response ceiling, which is the whole point.
    """
    pdf = pikepdf.Pdf.new()
    for n in range(1, pages + 1):
        body = [
            (72.0, 90.0 + i * 15, f"Page {n:03d} line {i + 1:02d}: {'lorem ipsum dolor sit amet ' * 2}", 10.0)
            for i in range(42)
        ]
        pdf.pages.append(_page(pdf, _text_ops(body)))
    return _save(pdf, name)


def encrypted(password: str = "secret", name: str = "encrypted.pdf") -> Path:
    """Opens only with the password. probe() must say so instead of failing."""
    pdf = pikepdf.Pdf.new()
    pdf.pages.append(_page(pdf, _text_ops([(72.0, 100.0, "Locked content.", 12.0)])))
    CORPUS.mkdir(parents=True, exist_ok=True)
    path = CORPUS / name
    pdf.save(path, encryption=pikepdf.Encryption(owner=password, user=password, R=6))
    return path


def damaged(name: str = "damaged.pdf") -> Path:
    """A PDF the fast reader cannot open and QPDF can recover -- what repair is for.

    The damage mode was chosen by MEASUREMENT, not by reasoning about what
    "corrupt" means, and the first three guesses were wrong. Against every
    reader in this stack:

        wrong startxref offset      pdfium OK   pikepdf OK   <- recovered
        content stream zeroed       pdfium OK   pikepdf OK   <- recovered
        truncated to 85%            pdfium FAILS pikepdf OK  <- THIS ONE
        corrupt %PDF header         pdfium FAILS pikepdf OK
        header + truncated          pdfium FAILS pikepdf OK

    So a corrupted cross-reference table -- the obvious choice, and what this
    fixture did first -- proves nothing: both libraries rebuild the xref
    silently and open the file. A fixture whose damage is invisible to every
    reader is not a damaged-file fixture, it is a second copy of the control.

    Truncation is the case that separates them, and separating them is exactly
    what optimize(action="repair") means here: the fast path (pdfium) refuses,
    QPDF recovers what is left. Both pages survive; the tail does not.
    """
    pdf = pikepdf.Pdf.new()
    for n in (1, 2):
        body = [(72.0, 100.0 + i * 16, f"Page {n} line {i + 1} of a file about to be broken.", 11.0) for i in range(20)]
        pdf.pages.append(_page(pdf, _text_ops(body)))
    CORPUS.mkdir(parents=True, exist_ok=True)
    path = CORPUS / name
    pdf.save(path)
    raw = path.read_bytes()
    path.write_bytes(raw[: int(len(raw) * 0.85)])
    return path


def clipped_table(name: str = "clipped_table.pdf") -> Path:
    """An unruled table whose content runs past the grid pdfplumber infers.

    `unruled_table.pdf` above proves the whitespace strategy gets the SHAPE
    wrong, which this repo accepts and reports as low confidence. This one
    proves something else: that it silently CUTS values.

    pdfplumber's text strategy derives a bounding box from the text it sees and
    then assigns characters to cells by position, discarding anything outside
    that box. Where a line extends past it -- here the 20pt title, which is
    wider than the body that set the box -- the cell comes back holding part of
    a word: `Quarterly R` / `epor`, and a header cell reading `Revenu`.

    That is not a guess about structure. It is a wrong value, and no confidence
    score tells a caller that a character is missing. Found on a
    LibreOffice-produced page where the cut fell inside a number and
    `1450.50` was returned as `1450.` -- an answer that looks entirely normal.

    The layout is deliberate and fragile in one direction only: the title must
    be set larger than the body, because it is the title overhanging the
    inferred box that does the cutting.
    """
    pdf = pikepdf.Pdf.new()
    items = [
        (57.0, 60.0, "Quarterly Report", 20.0),
        (57.0, 100.0, "Revenue grew across", 12.0),
        (57.0, 140.0, "Regional Detail", 16.0),
        (57.0, 180.0, "APAC led growth;", 12.0),
        (60.0, 220.0, "Region", 12.0),
        (113.0, 220.0, "Units", 12.0),
        (155.0, 220.0, "Revenue", 12.0),
        (58.0, 250.0, "APAC", 12.0),
        (112.0, 250.0, "120", 12.0),
        (153.0, 250.0, "1450.50", 12.0),
        (58.0, 280.0, "EMEA", 12.0),
        (112.0, 280.0, "95", 12.0),
        (153.0, 280.0, "1120.20", 12.0),
        (58.0, 310.0, "AMER", 12.0),
        (112.0, 310.0, "80", 12.0),
        (153.0, 310.0, "990.75", 12.0),
    ]
    pdf.pages.append(_page(pdf, _text_ops(items)))
    return _save(pdf, name)


def kerned(name: str = "kerned.pdf") -> Path:
    """A value drawn as a TJ array with kerning numbers between its digits.

    Every fixture above draws text with `(...) Tj` -- one string, no kerning --
    which is not what a word processor emits. Real producers set text with TJ,
    an array of strings interleaved with kerning adjustments, and the numbers
    are where redact() went wrong: pikepdf hands them back as Python ints, and
    `bytes(3)` is three NUL bytes rather than the digit 3, so each kern spliced
    NULs into the middle of the text being matched.

    The kern inside the value is POSITIVE, and that is the whole point. A
    negative kern makes `bytes(-30)` raise ValueError, which the old code
    caught and turned into an empty string -- so a fixture kerned only the
    usual way passes against the broken code and proves nothing. A positive one
    splices thirty NULs into the middle of the number instead.

    Both signs are ordinary. Measured across the corpus: 62 of 127 kerns in a
    LibreOffice-produced PDF are positive, and 4,572 of 9,397 in a real
    scanned-then-OCR'd invoice. This is not an exotic case; it is half of them.

    So the pattern is `1450.50` and the array is `(1450) 30 (.50)`: a matcher
    that keeps the kern sees `1450\\x00...\\x00.50` and finds nothing, while
    pdfium extracts `1450.50` and the caller is told the redaction failed.
    """
    pdf = pikepdf.Pdf.new()
    content = (
        b"BT\n/F1 12 Tf 1 0 0 1 72.00 700.00 Tm "
        b"[(Invoice total ) -25 (1450) 30 (.50) -25 ( due on receipt)] TJ\n"
        b"1 0 0 1 72.00 680.00 Tm (Reference 1120.20 is a separate figure.) Tj\nET"
    )
    stream = pdf.make_stream(zlib.compress(content))
    stream.Filter = pikepdf.Name.FlateDecode
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )
    pdf.pages.append(
        pikepdf.Page(
            pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Page,
                    MediaBox=[0, 0, W, H],
                    Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
                    Contents=pdf.make_indirect(stream),
                )
            )
        )
    )
    return _save(pdf, name)


# The text of the subset-font fixture. Carries a decimal number (the thing a
# redaction is usually asked for), a word, and enough distinct characters to
# make the glyph codes obviously not ASCII.
SUBSET_TEXT = "Account 1450.50 balance"


def subset_font(name: str = "subset_font.pdf") -> Path:
    """Text addressed by GLYPH INDEX, decodable only through /ToUnicode.

    Every other PDF fixture here stores its text as readable bytes, because
    they are drawn in Helvetica. Almost nothing real does: LibreOffice, Word
    and every modern producer embed a subset font and address it by glyph
    index, so `1450.50` reaches a content-stream tool as b'\\x07\\n\\n\\x0e...'
    and a byte-level match finds nothing.

    That gap is invisible without this fixture. redact() passed every test in
    this repo and could not redact a single document made by a word processor;
    it was found by converting an HTML file to PDF through LibreOffice in the
    container and trying, which is a thing no unit test does.

    No font file is embedded -- a viewer substitutes one and pdfium extracts
    through the /ToUnicode CMap, which is the property under test. Embedding a
    real TrueType subset would add a binary blob and a dependency for no extra
    coverage.
    """
    pdf = pikepdf.Pdf.new()
    # Codes start at 1: 0 is .notdef in a real subset, and using it would make
    # an unmapped code indistinguishable from a mapped one.
    alphabet = sorted(set(SUBSET_TEXT))
    code_of = {character: index + 1 for index, character in enumerate(alphabet)}

    cmap = [
        "/CIDInit/ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CMapName/Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<00> <FF>",
        "endcodespacerange",
        f"{len(alphabet)} beginbfchar",
        *(f"<{code_of[c]:02X}> <{ord(c):04X}>" for c in alphabet),
        "endbfchar",
        "endcmap",
        "CMapName currentdict /CMap defineresource pop",
        "end",
        "end",
    ]
    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor,
            FontName=pikepdf.Name("/AAAAAA+SubsetTest"),
            Flags=4,
            FontBBox=[-100, -200, 1000, 900],
            ItalicAngle=0,
            Ascent=800,
            Descent=-200,
            CapHeight=700,
            StemV=80,
        )
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.TrueType,
            BaseFont=pikepdf.Name("/AAAAAA+SubsetTest"),
            FirstChar=1,
            LastChar=len(alphabet),
            Widths=[500] * len(alphabet),
            FontDescriptor=descriptor,
            ToUnicode=pdf.make_indirect(pdf.make_stream("\n".join(cmap).encode("latin-1"))),
        )
    )

    encoded = bytes(code_of[c] for c in SUBSET_TEXT)
    escaped = encoded.replace(b"\\", b"\\\\").replace(b"(", rb"\(").replace(b")", rb"\)")
    content = b"BT\n/F1 14 Tf 1 0 0 1 72.00 700.00 Tm (" + escaped + b") Tj\nET"
    stream = pdf.make_stream(zlib.compress(content))
    stream.Filter = pikepdf.Name.FlateDecode
    pdf.pages.append(
        pikepdf.Page(
            pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Page,
                    MediaBox=[0, 0, W, H],
                    Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
                    Contents=pdf.make_indirect(stream),
                )
            )
        )
    )
    return _save(pdf, name)


BUILDERS = {
    "born_digital": born_digital,
    "two_column": two_column,
    "running_heads": running_heads,
    "hyphenated": hyphenated,
    "ruled_table": ruled_table,
    "unruled_table": unruled_table,
    "hybrid": hybrid,
    "encrypted": encrypted,
    "damaged": damaged,
    "clipped_table": clipped_table,
    "kerned": kerned,
    "subset_font": subset_font,
    "large": large,
}


def build_all(include_large: bool = True) -> dict[str, Path]:
    """Build every fixture. `large` is skipped where only shape matters."""
    out = {}
    for name, fn in BUILDERS.items():
        if name == "large" and not include_large:
            continue
        out[name] = fn()
    return out


if __name__ == "__main__":
    for name, path in build_all().items():
        print(f"{name:<16} {path.stat().st_size:>9,} bytes  {path}")
