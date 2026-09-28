"""A table whose header is ruled and whose rows are not.

An invoice draws its column headings in a ruled or shaded band and its line
items as plain text under it. The line strategy finds a one-row table -- the
header -- and extract_tables returned that alone: on all nine SuperStore
invoices in the corpus the whole table was `Item | Quantity | Rate | Amount`,
at 0.95 confidence, while read_page showed the item line right under it.

The header's cells are now the columns the rows under it are read into, while
the lines stay inside the table and close together; the gap above the totals
block ends it. The rows are aligned, not ruled, and the table says so.
"""

from __future__ import annotations

import pikepdf
import pytest

from servers.docs_read import engine as read
from tests.fixtures import build, real

HEADER = ["Item", "Quantity", "Rate", "Amount"]
COLS = [(30.0, 330.0), (330.0, 405.0), (405.0, 480.0), (480.0, 580.0)]


def _invoice(name: str = "header_band_invoice.pdf"):
    pdf = pikepdf.Pdf.new()
    top, band = 200.0, 22.0
    rects = [(a, top, b - a, band) for a, b in COLS]
    items = [(a + 8, top + 15, h, 10.0) for (a, _), h in zip(COLS, HEADER)]
    y = top + band + 18
    for desc, wrap, qty, rate, amount in [
        ("Novimex Executive Leather Armchair", "Chairs, Furniture, FUR-CH-5378", "2", "$911.04", "$1,822.08"),
        ("Eldon File Cart, Single Width", "Storage, Office Supplies", "7", "$808.73", "$5,661.12"),
    ]:
        items += [
            (COLS[0][0] + 8, y, desc, 10.0),
            (COLS[1][0] + 8, y, qty, 10.0),
            (COLS[2][0] + 8, y, rate, 10.0),
            (COLS[3][0] + 8, y, amount, 10.0),
        ]
        items.append((COLS[0][0] + 8, y + 15, wrap, 10.0))
        y += 36
    # The totals block, after a gap: not part of the table.
    items += [
        (400.0, y + 50, "Subtotal:", 10.0),
        (500.0, y + 50, "$7,483.20", 10.0),
        (420.0, y + 70, "Total:", 10.0),
        (500.0, y + 70, "$7,483.20", 10.0),
    ]
    pdf.pages.append(build._page(pdf, build._rect_ops(rects), build._text_ops(items)))
    return build._save(pdf, name)


@pytest.fixture(scope="module")
def invoice():
    return str(_invoice())


class TestTheRowsUnderARuledHeader:
    def test_the_items_are_rows_of_the_table(self, invoice):
        tables = read.extract_tables(invoice)["result"]["tables"]
        assert len(tables) == 1
        assert tables[0]["rows"] == [
            HEADER,
            ["Novimex Executive Leather Armchair Chairs, Furniture, FUR-CH-5378", "2", "$911.04", "$1,822.08"],
            ["Eldon File Cart, Single Width Storage, Office Supplies", "7", "$808.73", "$5,661.12"],
        ]

    def test_the_totals_after_the_gap_are_not(self, invoice):
        rows = read.extract_tables(invoice)["result"]["tables"][0]["rows"]
        assert not any("Subtotal" in cell or "Total" in cell for row in rows for cell in row)

    def test_it_says_the_rows_were_aligned_not_ruled(self, invoice):
        table = read.extract_tables(invoice)["result"]["tables"][0]
        assert table["confidence"] == 0.8
        assert "2 row(s) under it are not" in table["note"]

    def test_the_bbox_covers_the_rows(self, invoice):
        table = read.extract_tables(invoice)["result"]["tables"][0]
        assert table["bbox"][3] > 280, "the rows' words must count as the table's for to_markdown"


def test_a_fully_ruled_table_is_unchanged(tmp_path):
    table = read.extract_tables(str(build.ruled_table()))["result"]["tables"][0]
    assert (table["shape"], table["confidence"], "note" in table) == ([5, 3], 0.95, False)


def test_the_corpus_invoice(tmp_path):
    """invoice_009: the table the sweep found reduced to its header."""
    try:
        path = real.path("superstore_invoice")
    except KeyError:
        pytest.skip("no superstore_invoice in the real-corpus map")
    rows = read.extract_tables(str(path))["result"]["tables"][0]["rows"]
    assert rows[1] == [
        "Novimex Executive Leather Armchair, Adjustable Chairs, Furniture, FUR-CH-5378",
        "2",
        "$911.04",
        "$1,822.08",
    ]
