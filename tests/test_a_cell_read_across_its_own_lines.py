"""A ruled table cell holding wrapped text came back with its words shuffled.

`_rows_from_words` filled each cell with the whole words whose centre falls
inside it -- and then sorted them by `x0` alone. For a cell holding one line
that IS reading order, which is why every table fixture in this repo passed:
they all have one line per cell. A cell holding two lines was read ACROSS them:

    x0 order : Name: Role: Chair Ada
    reading  : Name: Ada Role: Chair

Measured over 54 corpus PDFs and 189,463 filled cells: 285 span more than one
line and **254 came back scrambled -- every one of them from a `ruled` table**,
which this module rates 0.95, the highest confidence in its vocabulary. None
from a whitespace table, which builds one row per text line and therefore
cannot produce a multi-line cell. A pharmaceutical supply agreement had 45 such
cells, each an entire contract clause turned into word salad, under
`success: true`.

The rule that shipped was chosen by measurement, not by argument. Four
candidates scored against pdfplumber's own `extract_text_lines()` -- a
different code path in the same library, which segments a page into lines
without knowing tables exist -- over 170 documents and 181,240 filled cells:

                                        agrees    single-line cells it changed
    sort by x0 (the bug)                99.12%      -
    sort by (top, x0)                   98.31%      2,874
    group tops within a measured 0.5pt  99.45%        932
    vertical OVERLAP (shipped)          99.63%          0

`(top, x0)` needs no constant and is the obvious fix; it is WORSE than the bug,
because a line set in two sizes has two different tops and gets split, then
ordered by type size. The 0.5pt figure was measured honestly -- words
pdfplumber puts on one line differ by 0.00pt at p99, and the step to the next
line is never under 0.62pt -- and it still disturbed 932 cells that were
already right, for the same reason. Overlap asks the question the eye asks and
cannot change a single-line cell at all.
"""

from __future__ import annotations

import pytest

from core.tables import _reading_order
from servers.docs_read import engine as read
from tests.fixtures import build


@pytest.fixture(scope="module")
def wrapped():
    return str(build.wrapped_cells())


def rows_of(path):
    payload = read.extract_tables(path)
    assert payload["success"], payload
    tables = payload["result"]["tables"]
    assert len(tables) == 1, tables
    return tables[0]


class TestTheCellReadsDownThenAcross:
    def test_the_basis_is_ruled(self, wrapped):
        """If this ever stops being `ruled`, the test below stops meaning anything."""
        assert rows_of(wrapped)["basis"] == "ruled"
        assert rows_of(wrapped)["confidence"] == 0.95

    def test_a_two_line_cell_reads_as_written(self, wrapped):
        table = rows_of(wrapped)
        body = [row for row in table["rows"] if row[0].startswith("Name:")]
        assert body, table["rows"]
        assert body[0][0] == "Name: Ada Role: Chair"
        assert body[0][1] == "Signed 2026 in Lisbon"

    def test_the_x0_order_is_not_what_comes_back(self, wrapped):
        """Named explicitly, because it is a plausible-looking wrong answer."""
        table = rows_of(wrapped)
        flat = " | ".join(cell for row in table["rows"] for cell in row)
        assert "Name: Role: Chair Ada" not in flat
        assert "Signed in Lisbon 2026" not in flat

    def test_a_single_line_cell_is_untouched(self, wrapped):
        """The control. For one line the two orders agree, and must still."""
        table = rows_of(wrapped)
        assert ["Field", "Detail"] in table["rows"]
        assert ["Term", "Three years"] in table["rows"]


class TestTheOrderingRuleItself:
    def word(self, text, x0, top, height=10.0):
        return {"text": text, "x0": x0, "x1": x0 + 20, "top": top, "bottom": top + height}

    def test_one_line_is_left_to_right(self):
        words = [self.word("c", 300, 100), self.word("a", 100, 100), self.word("b", 200, 100)]
        assert [w["text"] for w in _reading_order(words)] == ["a", "b", "c"]

    def test_two_lines_are_read_in_turn(self):
        words = [self.word("d", 100, 120), self.word("b", 300, 100), self.word("a", 100, 100)]
        assert [w["text"] for w in _reading_order(words)] == ["a", "b", "d"]

    def test_baseline_jitter_does_not_split_a_line(self):
        words = [self.word("b", 300, 100.4), self.word("a", 100, 100.0)]
        assert [w["text"] for w in _reading_order(words)] == ["a", "b"]

    def test_two_type_sizes_on_one_line_stay_on_one_line(self):
        """The case that beat every absolute tolerance. A 14pt word and a 9pt
        word on the same line have tops 5pt apart and still overlap."""
        big = self.word("Total", 100, 98.0, height=14.0)
        small = self.word("1.234", 300, 103.0, height=9.0)
        assert [w["text"] for w in _reading_order([small, big])] == ["Total", "1.234"]

    def test_a_real_line_break_does_split(self):
        words = [self.word("second", 100, 112.0), self.word("first", 300, 100.0)]
        assert [w["text"] for w in _reading_order(words)] == ["first", "second"]

    def test_no_word_is_lost_or_duplicated(self):
        words = [self.word(str(n), (n % 3) * 100, 100 + (n // 3) * 12) for n in range(9)]
        got = _reading_order(words)
        assert sorted(w["text"] for w in got) == sorted(w["text"] for w in words)
