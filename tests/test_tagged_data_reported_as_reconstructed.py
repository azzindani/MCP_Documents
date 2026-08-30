"""probe() called every format's structure `text_layer`, including tagged ones.

Found while adding the XBRL reader. An XBRL instance is the one format here
whose figures are not reconstructed from anything: the filer writes
`<Assets contextRef="CurrentYearInstant">1640830566000000</Assets>` into a
machine-readable field. Its reader sets `basis: "native"` on every page. probe
reported:

    {"format": "xbrl", ..., "basis": "text_layer"}

`basis` is this repo's central honesty mechanism, and `text_layer` is a
specific claim -- "glyphs the file actually contains" -- which is the right
answer for a PDF, whose paragraphs and tables are then RECONSTRUCTED from
coordinates and carry `ruled`, `whitespace` or `font_size` accordingly. It is
the wrong answer for a format that declares its own structure, and it is wrong
in the dangerous direction: a caller comparing a figure from the PDF against
the same figure from the XBRL was told both came from the same kind of source.

The cause was one constant. `_describe` ended:

    basis="text_layer" if digital else "empty"

so no reader's own verdict ever reached the response. **This was never about
XBRL.** All twelve non-PDF formats already set `native` and all twelve were
reported as `text_layer` -- HTML, Word, slides, worksheets, email, epub, text,
markdown and CSV. Adding a thirteenth is what made it visible, because it is
the first format where the difference between "declared" and "reconstructed"
changes what a caller should do with the number.

Fixed by taking the basis the pages actually carry, the same way `outline`
already does with its entries.
"""

from __future__ import annotations

import pytest

from core.readers import load_page, open_source
from servers.docs_read import engine as read
from tests.fixtures import build, build_formats, real

# Every format whose structure the FILE declares. Their readers all set
# `native`; none of them may be reported as `text_layer`.
DECLARED = [
    "article.html",
    "report.docx",
    "book.xlsx",
    "deck.pptx",
    "message.eml",
    "book.epub",
    "notes.txt",
    "readme.md",
    "sales.csv",
]


@pytest.fixture(scope="module")
def formats():
    return build_formats.build_all()


def _named(corpus, filename: str) -> str:
    for path in corpus.values():
        if str(path).endswith(filename):
            return str(path)
    pytest.skip(f"fixture {filename} not built")


class TestProbeReportsWhatTheReaderFound:
    @pytest.mark.parametrize("filename", DECLARED)
    def test_a_declared_format_is_native(self, formats, filename):
        source = _named(formats, filename)
        result = read.probe(source)
        assert result["success"] is True
        assert result["basis"] == "native", f"{filename} declares its structure; probe called it reconstructed"

    @pytest.mark.parametrize("filename", DECLARED)
    def test_probe_agrees_with_the_reader(self, formats, filename):
        """The response must not contradict the page it was built from."""
        source = _named(formats, filename)
        assert read.probe(source)["basis"] == load_page(open_source(source), 1).basis

    def test_a_pdf_is_still_text_layer(self):
        """The claim that was right all along, and must not have been widened.

        A PDF really is glyphs the file contains, and its structure really is
        reconstructed. If this turned `native` the fix would have replaced one
        untrue constant with another.
        """
        corpus = build.build_all(include_large=False)
        pdf = next(str(p) for p in corpus.values() if str(p).endswith(".pdf"))
        assert read.probe(pdf)["basis"] == "text_layer"

    def test_a_document_with_no_text_is_still_empty(self):
        """The other branch of the constant this replaced."""
        result = read.probe(str(real.path("scanned_invoice")))
        assert result["success"] is True
        assert result["basis"] == "empty"


class TestTheStrongerClaimIsTheTrueOne:
    def test_xbrl_facts_are_native_not_text_layer(self):
        """The format that made the defect visible.

        Read through the archive selector, because that is how a filing
        actually arrives -- the instance ships inside a zip.
        """
        source = f"{real.path('xbrl_instance_zip')}::instance.xbrl"
        result = read.probe(source)
        assert result["success"] is True
        assert result["result"]["format"] == "xbrl"
        assert result["basis"] == "native"

    @pytest.mark.parametrize("filename", ["article.html", "book.xlsx", "report.docx"])
    def test_find_and_extract_agree_with_probe(self, formats, filename):
        """probe was not the only tool with a hardcoded basis.

        `find` returned the constant `"text_layer"` and `extract` computed
        `"empty" if all scanned else "text_layer"`. Fixing probe alone would
        have left one tool honest and three not, on the same document.
        """
        source = _named(formats, filename)
        assert read.find(source, "e")["basis"] == "native"
        assert read.extract(source, pages="1")["basis"] == "native"

    def test_a_declared_table_is_not_summarised_as_a_guess(self, formats):
        """The worst of the four, and the one worth a test of its own.

        `extract_tables` summarised with `"ruled" if all ruled else
        "whitespace"` -- it knew two values. A table the format DECLARES fell
        into the else branch and the response said `whitespace`: the lowest
        confidence in the vocabulary, on the one kind of table that involves no
        inference. The tables inside the same response said
        `basis: "native", confidence: 1.0`, so it contradicted its own contents.
        """
        source = _named(formats, "article.html")
        result = read.extract_tables(source)
        assert result["success"] is True
        assert result["result"]["count"] >= 1
        assert {t["basis"] for t in result["result"]["tables"]} == {"native"}
        assert result["basis"] == "native", "a declared table was summarised as an inferred one"

    def test_a_pdf_table_still_reports_how_it_was_found(self):
        """The distinction the summary exists for must survive the fix."""
        corpus = build.build_all(include_large=False)
        ruled = next((str(p) for p in corpus.values() if str(p).endswith("ruled_table.pdf")), None)
        if ruled is None:
            pytest.skip("ruled_table.pdf not built")
        assert read.extract_tables(ruled, pages="1")["basis"] == "ruled"

    def test_a_mixed_set_reports_the_weakest(self):
        """A summary is a claim about every item, so it takes the worst one."""
        from core.ir import weakest_basis

        assert weakest_basis(["native", "whitespace"]) == "whitespace"
        assert weakest_basis(["ruled", "native"]) == "ruled"
        assert weakest_basis(["native", "native"]) == "native"
        assert weakest_basis([]) == "text_layer"
        assert weakest_basis(["nonsense"], fallback="whitespace") == "whitespace"

    def test_the_same_filing_reports_two_different_bases(self):
        """The PDF and the XBRL of ONE filing, and they must not match.

        This is the assertion the whole field exists for. The same company's
        same quarter, read two ways: one reconstructed from glyph positions,
        one stated by the filer. A caller choosing between them is entitled to
        be told which is which.
        """
        from_pdf = read.probe(str(real.path("hybrid_financial")))
        from_xbrl = read.probe(f"{real.path('xbrl_instance_zip')}::instance.xbrl")
        assert from_pdf["basis"] == "text_layer"
        assert from_xbrl["basis"] == "native"
        assert from_pdf["basis"] != from_xbrl["basis"]
