"""to_markdown() returned prose for a document that declares its structure.

Found by Phase 5's remote smoke test, asking for an HTML file as markdown. The
answer was `success: true`, `headings: 0`, `basis: "text_layer"`, and one
run-together paragraph:

    Quarterly Review Revenue rose across every region ... Costs Input prices
    eased ... Region Revenue Change\\nNorth 412 +8%

while `outline()` on the same file, in the same server, listed both headings
correctly. Two tools disagreeing about one document, and neither reporting an
error.

Three faults stacked, from the bottom up:

**`lines_with_size` had no geometry guard.** Its twin `lines_from_blocks`
returns one line per block when no block has a bbox; this one did not, so every
block of a page-less format (HTML, txt, md, csv, docx, epub, email) was tested
against `abs(y - last_y) <= 2.0` with every y equal to 0.0 and the whole page
collapsed onto ONE line. Nothing downstream could recover the paragraphs, and
heading detection had a single 300-character "line" to judge.

**Declared headings were re-derived from type size.** The HTML reader maps
`<h2>` onto a size so the PDF machinery can see it, and `to_markdown` then
inferred a heading back out of that size -- a guess standing in for a fact the
reader already had, reported as `basis: "font_size"`. `outline()` had been
fixed to say `native` for exactly these formats; `to_markdown` had not.

**Declared tables were emitted as their flattening.** A `<table>` arrives as
one block carrying its cells on `Block.rows`, and the tool wrote out the
tab-and-newline flattening instead -- which is not markdown, from a tool named
`to_markdown`.
"""

from __future__ import annotations

import pytest

from core import order
from core.ir import Block, Span
from servers.docs_read import engine as read
from tests.fixtures import build, build_formats


@pytest.fixture(scope="session")
def formats():
    return build_formats.build_all()


@pytest.fixture(scope="session")
def corpus():
    return build.build_all(include_large=False)


class TestLinesWithSizeKeepsBlocksApart:
    """The unit fault, isolated from every tool that suffered from it."""

    def _blocks(self, *texts: str) -> list[Block]:
        return [Block(kind="para", spans=[Span(text=t, size=12.0)], bbox=None) for t in texts]

    def test_blocks_with_no_geometry_are_separate_lines(self):
        lines = order.lines_with_size(self._blocks("first", "second", "third"))
        assert [text for text, _ in lines] == ["first", "second", "third"]

    def test_it_agrees_with_its_own_twin(self):
        """The two are documented as the same grouping. They must produce it.

        Written as a comparison rather than two literals so the pair cannot
        drift apart again -- which is exactly how this happened.
        """
        blocks = self._blocks("alpha", "beta", "gamma")
        assert [text for text, _ in order.lines_with_size(blocks)] == order.lines_from_blocks(blocks)

    def test_each_line_keeps_its_own_size(self):
        blocks = [
            Block(kind="heading", spans=[Span(text="Title", size=26.0)], bbox=None),
            Block(kind="para", spans=[Span(text="Body text here.", size=12.0)], bbox=None),
        ]
        assert order.lines_with_size(blocks) == [("Title", 26.0), ("Body text here.", 12.0)]

    def test_positioned_blocks_still_group_by_baseline(self):
        """The PDF path is unchanged: two words on one baseline are one line."""
        blocks = [
            Block(kind="para", spans=[Span(text="left", size=11.0)], bbox=(10.0, 100.0, 40.0, 112.0)),
            Block(kind="para", spans=[Span(text="right", size=11.0)], bbox=(50.0, 100.5, 80.0, 112.0)),
            Block(kind="para", spans=[Span(text="below", size=11.0)], bbox=(10.0, 130.0, 40.0, 142.0)),
        ]
        assert [text for text, _ in order.lines_with_size(blocks)] == ["left right", "below"]


class TestHtmlBecomesRealMarkdown:
    def test_a_declared_heading_is_a_markdown_heading(self, formats):
        payload = read.to_markdown(str(formats["article_html"]))
        assert payload["success"], payload
        assert "# Quarterly Review" in payload["result"]["markdown"]
        assert "## Costs" in payload["result"]["markdown"]

    def test_the_heading_count_is_not_zero(self, formats):
        payload = read.to_markdown(str(formats["article_html"]))
        assert payload["result"]["headings"] >= 2

    def test_the_level_comes_from_the_tag_not_from_a_size(self, formats):
        """`<h1>` is one hash and `<h2>` is two, because the markup said so."""
        markdown = read.to_markdown(str(formats["article_html"]))["result"]["markdown"]
        assert "\n## Costs" in markdown or markdown.startswith("## Costs")
        assert "### Costs" not in markdown

    def test_the_basis_says_native_rather_than_font_size(self, formats):
        """A declared heading is a fact; reporting it as a guess loses that.

        outline() makes this distinction on the same file. The two tools
        disagreeing about one document is worse than either being imprecise.
        """
        payload = read.to_markdown(str(formats["article_html"]))
        assert payload["basis"] == "native"
        assert read.outline(str(formats["article_html"]))["basis"] == "native"

    def test_the_paragraphs_are_not_run_together(self, formats):
        markdown = read.to_markdown(str(formats["article_html"]))["result"]["markdown"]
        assert "Quarterly Review Revenue rose" not in markdown

    def test_a_declared_table_is_a_markdown_table(self, formats):
        markdown = read.to_markdown(str(formats["article_html"]))["result"]["markdown"]
        assert "| Region | Revenue | Change |" in markdown
        assert "| --- | --- | --- |" in markdown
        assert "| North | 412 | +8% |" in markdown

    def test_the_table_count_is_reported(self, formats):
        assert read.to_markdown(str(formats["article_html"]))["result"]["tables"] >= 1


class TestOtherDeclaringFormats:
    def test_markdown_headings_survive_a_round_trip(self, formats):
        """A .md file's own `#` lines must come back as `#` lines."""
        payload = read.to_markdown(str(formats["readme_md"]))
        assert payload["success"], payload
        assert payload["result"]["headings"] >= 1
        assert payload["basis"] == "native"

    def test_a_docx_heading_style_is_a_heading(self, formats):
        payload = read.to_markdown(str(formats["report_docx"]))
        assert payload["success"], payload
        assert payload["result"]["markdown"].lstrip().startswith("#")
        assert payload["basis"] == "native"


class TestThePdfPathIsUnchanged:
    """A PDF declares nothing, so it must still infer -- and still say so."""

    def test_a_pdf_with_no_structure_reports_an_inferred_basis(self, corpus):
        payload = read.to_markdown(str(corpus["born_digital"]))
        assert payload["success"], payload
        assert payload["basis"] in {"font_size", "text_layer"}

    def test_a_two_column_pdf_still_reads_down_the_columns(self, corpus):
        """The geometry path is the one lines_with_size already handled."""
        payload = read.to_markdown(str(corpus["two_column"]), pages="1")
        assert payload["success"], payload
        markdown = payload["result"]["markdown"]
        assert "LEFT p1 line 1" in markdown
        assert markdown.index("LEFT p1 line 2") < markdown.index("RIGHT p1 line 1")
