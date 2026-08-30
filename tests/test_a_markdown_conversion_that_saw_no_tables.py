"""to_markdown reported `tables: 0` for a page that is nothing but a table.

Page 6 of the committed bank filing is a consolidated balance sheet. Asked
about that one page, in one session, this server gave three answers:

    extract_tables(pages='6')  ->  count: 1, shape [38, 9], basis whitespace
    read_page(6)               ->  one table, same shape, in result.tables
    to_markdown(pages='6')     ->  "tables": 0, and no table in the markdown

`to_markdown` rendered only tables the FORMAT DECLARES -- an HTML `<table>`, a
docx `w:tbl`, a worksheet -- and a PDF declares none: its tables are
reconstructed by core/tables.py from ruling lines or column gaps. So no PDF
table was ever rendered, and the count of what HAD been rendered was published
under the name `tables`.

Measured: 21 of 21 sampled pages of that filing where `extract_tables` finds a
table and `to_markdown` says zero. The control that proves the field means what
it says elsewhere is HTML and XLSX, where the two tools agree exactly.

The count is the worse half. A caller who converts a financial statement and
reads `tables: 0` concludes there is nothing to extract and never calls
`extract_tables`. `convert(to='md')` routes through this same function.

Two things now happen, and the split between them is the module's usual one.
A `ruled` table is the file's own grid, so it is spliced into the markdown --
the word blocks it covers come out, so no cell is printed twice. A `whitespace`
table's shape is inferred from column gaps at 0.5 confidence, and a markdown
pipe table is an assertion that the grid is real, so it is left as text and
`tables_left_as_text` says so and names the tool that has the rows.

The two-row, two-column floor was learned by rendering the result and reading
it: pdfplumber's line strategy calls the two ruled boxes on the filing's COVER
page a 2x1 table, and rendering that turned a readable title page into a
one-column markdown table holding two paragraphs.
"""

from __future__ import annotations

import pytest

from servers.docs_read import engine as read
from tests.fixtures import build, build_formats, real


@pytest.fixture(scope="module")
def corpus():
    return build.build_all(include_large=False)


@pytest.fixture(scope="module")
def formats():
    return build_formats.build_all()


class TestARuledTableReachesTheMarkdown:
    def test_it_is_rendered_as_a_pipe_table(self, corpus):
        payload = read.to_markdown(str(corpus["ruled_table"]))
        assert payload["success"], payload
        result = payload["result"]
        assert result["tables"] == 1
        assert "| Item | Qty | Price |" in result["markdown"]
        assert "| --- |" in result["markdown"]

    def test_the_cells_are_not_also_printed_as_loose_text(self, corpus):
        """The words the grid covers come out of the flow, or every cell appears twice."""
        markdown = read.to_markdown(str(corpus["ruled_table"]))["result"]["markdown"]
        assert markdown.count("Sprocket") == 1
        assert markdown.count("44.25") == 1

    def test_a_wrapped_cell_arrives_in_reading_order(self, corpus):
        markdown = read.to_markdown(str(build.wrapped_cells()))["result"]["markdown"]
        assert "| Name: Ada Role: Chair |" in markdown

    def test_the_count_agrees_with_extract_tables(self, corpus):
        """For a file that rules its grid, the two tools now say the same number."""
        path = str(corpus["ruled_table"])
        assert read.to_markdown(path)["result"]["tables"] == read.extract_tables(path)["result"]["count"] == 1


class TestAnInferredTableIsNamedRatherThanAsserted:
    def test_it_is_not_rendered_as_a_grid(self, corpus):
        result = read.to_markdown(str(corpus["unruled_table"]))["result"]
        assert result["tables"] == 0
        assert "| --- |" not in result["markdown"]

    def test_but_the_response_says_what_the_count_means(self, corpus):
        result = read.to_markdown(str(corpus["unruled_table"]))["result"]
        assert "extract_tables(" in result["tables_note"]

    def test_the_content_is_still_in_the_markdown(self, corpus):
        """Left as text is not dropped. The cells are all still readable."""
        markdown = read.to_markdown(str(corpus["unruled_table"]))["result"]["markdown"]
        for value in ("Sprocket", "44.25", "Widget"):
            assert value in markdown

    def test_the_note_is_never_a_count(self, corpus):
        """Measured, and it is why there is no second number here.

        pdfplumber's text strategy proposes a grid for essentially every page,
        so counting the unrendered ones reported a three-page prose document as
        holding three tables -- an over-claim replacing an under-claim. And the
        proposals cannot be told apart by shape: on this corpus a page of prose
        has 100% of its rows holding two or more cells and a real balance sheet
        has 81%.
        """
        result = read.to_markdown(str(corpus["born_digital"]))["result"]
        assert "tables_left_as_text" not in result
        assert result["tables"] == 0
        assert "reconstructed" in result["tables_note"]

    def test_a_format_with_no_tables_at_all_says_nothing(self, formats):
        """Plain text has no table machinery, so it must carry no note."""
        result = read.to_markdown(str(formats["plain_txt"]))["result"]
        assert result["tables"] == 0
        assert "tables_note" not in result


class TestTheFormatsThatDeclareTheirTablesAreUnchanged:
    """The control. These agreed with extract_tables before the fix and must still."""

    def test_html(self, formats):
        path = str(formats["article_html"])
        assert read.to_markdown(path)["result"]["tables"] == read.extract_tables(path)["result"]["count"]

    def test_docx(self, formats):
        path = str(formats["report_docx"])
        rendered = read.to_markdown(path)["result"]
        assert rendered["tables"] == read.extract_tables(path)["result"]["count"]
        assert "tables_left_as_text" not in rendered


class TestTheFilingItWasFoundOn:
    def test_the_balance_sheet_page_no_longer_reads_as_table_free(self):
        """`tables: 0` with nothing beside it is what sent a caller away."""
        result = read.to_markdown(str(real.path("hybrid_financial")), pages="6")["result"]
        assert "extract_tables(pages='6')" in result["tables_note"]

    def test_the_note_names_the_pages_that_were_converted(self):
        path = str(real.path("hybrid_financial"))
        result = read.to_markdown(path, pages="6-7")["result"]
        assert "extract_tables(pages='6-7')" in result["tables_note"]

    def test_the_cover_page_frame_is_not_rendered_as_a_table(self):
        """A 2x1 grid is a box. Rendering it cost the title page its line breaks."""
        result = read.to_markdown(str(real.path("hybrid_financial")), pages="1")["result"]
        assert result["tables"] == 0
        assert "| --- |" not in result["markdown"]
        assert "31 MARET/\nMARCH 2026" in result["markdown"]
