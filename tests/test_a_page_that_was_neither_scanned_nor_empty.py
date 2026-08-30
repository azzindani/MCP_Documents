"""probe() reported `scanned: 0` for a document that was nothing but a scan.

Found by round 1's sweep. `probe` of a 1 MB scanned invoice, one page, zero
extractable characters:

    "page_kinds": {"born_digital": 0, "scanned": 0, "empty": 1},
    "scanned_pages": "1",

Two fields of one response disagreeing about the same page, and the one a
caller branches on to decide whether to OCR is the wrong one. Confirmed against
the file: the page carries an image XObject and a 44-byte content stream. It is
a scan, not a blank.

The cause was a double count. A page with `char_count == 0` was appended to
BOTH the `empty` and `scanned` lists, and the reported figure was
`len(scanned) - len(empty)` — so only a page holding between 1 and 31
characters (`MIN_CHARS_FOR_TEXT_LAYER`) could survive the subtraction, and a
real scan extracts exactly zero.

**Why 216 tests missed it.** The `hybrid` fixture's pages measure
`[769, 769, 0, 1]`. Its page 4 carries a single burned-in page number, so it
lands in the 1..31 band and is counted correctly; `page_kinds` reads
`{born_digital: 2, scanned: 1, empty: 1}` and looks right. The fixture masks
the defect precisely because it was built to be realistic in a different way.

The three buckets are now mutually exclusive and sum to the page count, and
what separates the last two is ink rather than characters: `Page.has_content`,
which the PDF reader answers by counting the page's drawable objects. A format
with no pixels at all defaults to False, so an empty HTML page is never
reported as something OCR could rescue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.readers import load_page, open_source
from servers.docs_read import engine as read
from tests.fixtures import build, build_formats


@pytest.fixture(scope="session")
def mixed() -> Path:
    """page 1 born-digital, page 2 drawn but textless, page 3 truly blank."""
    return build.blank_and_scanned()


class TestTheFixtureHasThePropertyItExistsFor:
    def test_pages_two_and_three_both_extract_nothing(self, mixed):
        """They must be indistinguishable by character count, or the test is
        checking something easier than the defect."""
        doc = open_source(str(mixed))
        assert load_page(doc, 2).char_count == 0
        assert load_page(doc, 3).char_count == 0

    def test_but_only_page_two_has_ink(self, mixed):
        doc = open_source(str(mixed))
        assert load_page(doc, 2).has_content is True
        assert load_page(doc, 3).has_content is False


class TestTheBucketsAreExclusiveAndHonest:
    def test_a_drawn_page_is_scanned_and_a_blank_one_is_empty(self, mixed):
        kinds = read.probe(str(mixed))["result"]["page_kinds"]
        assert kinds == {"born_digital": 1, "scanned": 1, "empty": 1}

    def test_they_sum_to_the_page_count(self, mixed):
        """They used to overlap and be subtracted apart, so they could not."""
        result = read.probe(str(mixed))["result"]
        assert sum(result["page_kinds"].values()) == result["pages"]

    def test_scanned_pages_names_the_scan_and_not_the_blank(self, mixed):
        """A caller must not be sent to OCR a page with nothing on it."""
        assert read.probe(str(mixed))["result"]["scanned_pages"] == "2"

    def test_the_count_and_the_page_list_agree(self, mixed):
        """The exact contradiction the sweep found, as one assertion."""
        result = read.probe(str(mixed))["result"]
        named = [p for p in result["scanned_pages"].split(",") if p]
        assert len(named) == result["page_kinds"]["scanned"]

    def test_a_blank_page_still_counts_against_extractable(self, mixed):
        """`extractable` is about text, and a blank page yields none of it."""
        assert read.probe(str(mixed))["result"]["extractable"] == "partial"


class TestADocumentThatIsOnlyAScan:
    @pytest.fixture()
    def scan_only(self, tmp_path):
        import pikepdf

        from tests.fixtures.build import _page

        pdf = pikepdf.Pdf.new()
        pdf.pages.append(_page(pdf, b"0.5 g\n72 200 400 300 re f"))
        path = tmp_path / "scan_only.pdf"
        pdf.save(path)
        return path

    def test_it_is_reported_as_scanned_not_empty(self, scan_only):
        result = read.probe(str(scan_only))["result"]
        assert result["page_kinds"] == {"born_digital": 0, "scanned": 1, "empty": 0}
        assert result["extractable"] == "none"


class TestFormatsWithNoPixels:
    def test_an_html_page_is_never_reported_as_scannable(self):
        """`has_content` defaults False, so a flow format cannot claim OCR
        would help — there is nothing to point it at."""
        formats = build_formats.build_all()
        result = read.probe(str(formats["article_html"]))["result"]
        assert result["page_kinds"]["scanned"] == 0


class TestTheOldFixtureStillReadsCorrectly:
    def test_hybrid_is_unchanged(self):
        """Its page 4 has one stray glyph, which is still a scan."""
        corpus = build.build_all(include_large=False)
        result = read.probe(str(corpus["hybrid"]))["result"]
        assert sum(result["page_kinds"].values()) == result["pages"]
        assert result["page_kinds"]["born_digital"] == 2
        assert result["page_kinds"]["scanned"] >= 1
