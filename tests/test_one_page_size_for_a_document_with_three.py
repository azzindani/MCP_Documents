"""probe reported page 1's geometry as the document's, and said nothing more.

The BBCA filing is not one shape. 178 pages are US Letter portrait, one is A4,
and four are LANDSCAPE carrying /Rotate 90 -- and those four hold the widest
tables in the document, which is exactly what a caller needs to know before
asking for them. probe said:

    "page_size": [612.0, 792.0]

and nothing else. True of 178 pages, false of five, and indistinguishable from
a document where it is true of all 183.

This is the per-document verdict the module rejects everywhere else. `scanned`
is not a boolean here for precisely this reason -- a hybrid PDF is born-digital
on page 1 and a scan on page 40, so it reports counts and a selection string.
Geometry was the one property still answering for the whole document from a
sample of one page, and the sample was not even chosen: it was page 1 because
page 1 is first.

Two changes. The size reported is now the most COMMON one rather than the
first, because a cover page is routinely a different size from the body and the
answer should describe the document. And when the document holds more than one
size, `pages_of_other_size` names the rest, in the same selection-string form
as `scanned_pages`, so it can be pasted into the next call.

The field is omitted entirely for a uniform document. `pages_of_other_size: ""`
would invite the reading that some pages differ and the server could not say
which.
"""

from __future__ import annotations

import pytest

from servers.docs_read import engine as read
from tests.fixtures import build, real


@pytest.fixture(scope="module")
def filing():
    return str(real.path("hybrid_financial"))


# Measured with pypdfium2, not taken from probe: 178 x (612, 792),
# 1 x (595.4, 842.4), 4 x (792, 612).
BODY_SIZE = [612.0, 792.0]
OTHER_PAGES = "2,10-11,180-181"


class TestTheSizeDescribesTheDocument:
    def test_the_common_size_is_reported(self, filing):
        assert read.probe(filing)["result"]["page_size"] == BODY_SIZE

    def test_the_pages_that_differ_are_named(self, filing):
        result = read.probe(filing)["result"]
        assert result["pages_of_other_size"] == OTHER_PAGES

    def test_the_landscape_pages_are_among_them(self, filing):
        """The four rotated pages are the reason this matters at all."""
        named = read.probe(filing)["result"]["pages_of_other_size"]
        for page in (10, 11, 180, 181):
            assert str(page) in named or "10-11" in named or "180-181" in named

    def test_it_agrees_with_an_independent_reader(self, filing):
        """Against pypdfium2, not against another of this server's answers."""
        import collections

        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(filing)
        sizes = collections.Counter(tuple(round(v, 1) for v in pdf[i].get_size()) for i in range(len(pdf)))
        common, count = sizes.most_common(1)[0]
        assert list(common) == BODY_SIZE
        assert len(pdf) - count == 5, "the fixture no longer has five odd-sized pages"

        result = read.probe(filing)["result"]
        assert result["page_size"] == list(common)

    def test_the_warning_says_how_many_and_which(self, filing):
        messages = " ".join(str(step) for step in read.probe(filing)["progress"])
        assert "5 page(s) are not" in messages
        assert OTHER_PAGES in messages


class TestAUniformDocumentSaysNothingExtra:
    def test_the_field_is_absent(self):
        """An empty selection string would read as "some differ, unknown which"."""
        corpus = build.build_all(include_large=False)
        uniform = next((str(p) for p in corpus.values() if str(p).endswith("ruled_table.pdf")), None)
        if uniform is None:
            pytest.skip("ruled_table.pdf not built")
        result = read.probe(uniform)["result"]
        assert result["page_size"] == BODY_SIZE
        assert "pages_of_other_size" not in result

    def test_a_format_with_no_geometry_still_reports_none(self):
        """HTML has no page size; None says so, and [0.0, 0.0] would not."""
        from tests.fixtures import build_formats

        corpus = build_formats.build_all()
        html = next((str(p) for p in corpus.values() if str(p).endswith("article.html")), None)
        if html is None:
            pytest.skip("article.html not built")
        result = read.probe(html)["result"]
        assert result["page_size"] is None
        assert "pages_of_other_size" not in result
