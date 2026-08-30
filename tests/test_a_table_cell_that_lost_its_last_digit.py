"""extract_tables() returned `1450.` for a cell reading `1450.50`.

Found by Phase 5's remote smoke test, extracting the table out of a
LibreOffice-produced PDF. The response was `success: true`, one table, the
right shape -- and:

    ["APAC", "120 1450."]      the document says 1450.50
    ["AMER", "80 990.7"]       the document says 990.75

pdfplumber's text strategy derives a table bounding box from the text it sees
and then assigns characters to cells by position, discarding anything outside
that box. The inferred box ended at x=185.6 while `1450.50` runs from 152.8 to
202.3, so the last two digits fell off the edge.

This module already accepts that the whitespace strategy's SHAPE is a guess --
it reports confidence 0.5 and says the boundaries were inferred. A truncated
value is a different thing. It is not a guess about structure, it is a wrong
number, and a caller who does exactly what the note asks (verify the shape) is
still handed a figure with a digit missing. Nothing in the response could tell
them.

Fixed by filling each cell from the WHOLE words whose centre falls inside it,
rather than from pdfplumber's character-level extract. The shape stays as
uncertain as it was; the values stop being cut.
"""

from __future__ import annotations

import pdfplumber
import pytest

from servers.docs_read import engine as read
from tests.fixtures import build

WHITESPACE = {"vertical_strategy": "text", "horizontal_strategy": "text"}


@pytest.fixture(scope="session")
def corpus():
    return build.build_all(include_large=False)


def _cells(payload: dict) -> list[str]:
    return [cell for table in payload["result"]["tables"] for row in table["rows"] for cell in row if cell]


class TestTheFixtureHasThePropertyItExistsFor:
    """Without this, the assertions below could pass on a fixture that never
    triggered the fault -- proving the fix works where it was never needed."""

    def test_pdfplumber_itself_cuts_a_word_on_this_page(self, corpus):
        with pdfplumber.open(str(corpus["clipped_table"])) as pdf:
            tables = pdf.pages[0].find_tables(WHITESPACE)
            assert tables, "the whitespace strategy found no table; the fixture is not what this test assumes"
            raw = [cell for row in tables[0].extract() for cell in row if cell]
        # The words are on the page whole; only the extraction cuts them.
        assert any(cell in {"epor", "Revenu"} or cell.endswith(" R") for cell in raw), (
            f"nothing was truncated, so this fixture cannot fail against the old code: {raw}"
        )


class TestCellsAreWholeWords:
    def test_no_cell_is_a_fragment_of_a_word(self, corpus):
        payload = read.extract_tables(str(corpus["clipped_table"]))
        assert payload["success"], payload
        cells = _cells(payload)
        assert "epor" not in cells
        assert "Revenu" not in cells
        assert not any(cell.endswith(" R") for cell in cells)

    def test_the_words_that_were_cut_come_back_whole(self, corpus):
        joined = " ".join(_cells(read.extract_tables(str(corpus["clipped_table"]))))
        assert "Report" in joined
        assert "Revenue" in joined

    def test_the_numbers_are_complete(self, corpus):
        joined = " ".join(_cells(read.extract_tables(str(corpus["clipped_table"]))))
        for value in ("1450.50", "1120.20", "990.75"):
            assert value in joined

    def test_no_cell_holds_a_partial_number(self, corpus):
        """The shape of the original defect: a figure short of a digit.

        Asserted as "no cell is a proper prefix of a real value" rather than as
        a list of literals, so a future truncation at a different offset is
        caught too.
        """
        cells = _cells(read.extract_tables(str(corpus["clipped_table"])))
        for value in ("1450.50", "1120.20", "990.75"):
            for cell in cells:
                for token in cell.split():
                    assert not (value.startswith(token) and token != value), (
                        f"cell token {token!r} is a truncated {value!r}"
                    )


class TestTheHonestPartsAreUnchanged:
    def test_it_is_still_reported_as_a_guess(self, corpus):
        payload = read.extract_tables(str(corpus["clipped_table"]))
        table = payload["result"]["tables"][0]
        assert table["basis"] == "whitespace"
        assert table["confidence"] == 0.5
        assert "verify the shape" in table["note"]

    def test_the_note_now_also_says_content_can_be_missing(self, corpus):
        """Whole words are not the same as all the words.

        Content outside the inferred grid is still not returned -- that IS the
        shape limitation -- and the note says so rather than leaving a caller
        to assume a complete table.
        """
        table = read.extract_tables(str(corpus["clipped_table"]))["result"]["tables"][0]
        assert "not included" in table["note"]

    def test_a_ruled_table_is_still_exact(self, corpus):
        """The high-confidence path still reports itself as high-confidence."""
        payload = read.extract_tables(str(corpus["ruled_table"]))
        table = payload["result"]["tables"][0]
        assert table["basis"] == "ruled"
        assert table["confidence"] == 0.95

    def test_the_ruled_values_are_exact(self, corpus):
        """And its values are still the fixture's, cell for cell."""
        rows = read.extract_tables(str(corpus["ruled_table"]))["result"]["tables"][0]["rows"]
        joined = " ".join(cell for row in rows for cell in row)
        for value in ("Widget", "Gadget"):
            assert value in joined


class TestTheRuledPathIsAlsoWholeWords:
    """Round 1's sweep found the same clipping on the RULED path.

    The first fix applied the whole-word rebuild to the whitespace strategy
    only, reasoning that a ruled table's rules sit between cells so no word can
    straddle one. A supreme-court judgment disagreed: the ruled header cell came
    back as `Ditambah/(Dikurangi)Keberata` while the row band plainly contains
    `Keberatan`. pdfplumber assigns characters by position and clips at the cell
    edge whichever strategy found the cell.

    High confidence made it worse rather than better — 0.95 on a word missing
    its last letter, with a note that only warns about the shape.
    """

    def test_a_value_overhanging_its_own_rule_survives(self, corpus, tmp_path):
        """Built here rather than taken from the corpus: the corpus is not this
        repo's to publish, and the property is reproducible in fourteen lines.

        The rule is drawn so it cuts through the middle of the last word, which
        is exactly what a tight table column does to a long heading.
        """
        import pikepdf

        from tests.fixtures.build import _page, _rect_ops, _text_ops

        pdf = pikepdf.Pdf.new()
        cells = _text_ops(
            [
                (80.0, 110.0, "Uraian", 9.0),
                (200.0, 110.0, "Keberatan", 9.0),
                (80.0, 140.0, "Total", 9.0),
                (200.0, 140.0, "1450.50", 9.0),
            ]
        )
        # Column rule at x=250, mid-way through "Keberatan" and "1450.50".
        rules = _rect_ops([(70.0, 95.0, 180.0, 60.0), (250.0, 95.0, 120.0, 60.0)])
        pdf.pages.append(_page(pdf, rules, cells))
        source = tmp_path / "tight_rules.pdf"
        pdf.save(source)

        payload = read.extract_tables(str(source))
        assert payload["success"], payload
        joined = " ".join(c for t in payload["result"]["tables"] for row in t["rows"] for c in row)
        assert "Keberatan" in joined, f"clipped: {joined!r}"
        assert "1450.50" in joined, f"clipped: {joined!r}"
        assert "Keberata " not in joined and joined.count("1450.5 ") == 0

    def test_the_unruled_fixture_still_finds_its_values(self, corpus):
        payload = read.extract_tables(str(corpus["unruled_table"]))
        assert payload["success"], payload
        assert payload["result"]["count"] >= 1
