"""find() searches a form in the order it reads, not the order it was drawn.

A SuperStore invoice draws its amounts before their labels: the stream holds
"$636.24 $82.40 $718.64" and further on "Subtotal: Shipping: Total:". extract()
showed "Total: $718.64", find() searched the stream, and a regex written from
what the caller had just read -- `Total:\\s*\\$(?P<total>...)` -- matched on 0 of
9 invoices. A page whose stream climbs back up the page more than a few times
is now searched line by line; one that already reads top to bottom (a two-column
page in column order included) is searched exactly as before.
"""

from __future__ import annotations

import pikepdf

from core.readers import load_page, open_source, search_text
from servers.docs_read import engine as read
from tests.fixtures import build, real

TOTAL = r"\bTotal:\s*\$(?P<total>[\d,]+\.\d{2})"


def _drawn_values_first(tmp_path):
    """Values drawn first, then their labels, each bottom row first -- as a generated invoice draws its totals.

    Every step from one row to the next climbs back up the page: eight climbs.
    """
    rows = [
        ("Date:", "Aug 01 2012"),
        ("Balance Due:", "$1,906.05"),
        ("Subtotal:", "$1,822.08"),
        ("Shipping:", "$83.97"),
        ("Total:", "$1,906.05"),
    ]
    values = [(300.0, 100.0 + 22 * i, value, 11.0) for i, (_, value) in enumerate(rows)][::-1]
    labels = [(200.0, 100.0 + 22 * i, label, 11.0) for i, (label, _) in enumerate(rows)][::-1]
    pdf = pikepdf.Pdf.new()
    pdf.pages.append(build._page(pdf, build._text_ops(values), build._text_ops(labels)))
    path = tmp_path / "values_first.pdf"
    pdf.save(path)
    return path


def test_a_label_and_its_value_are_found_together(tmp_path):
    r = read.find(str(_drawn_values_first(tmp_path)), TOTAL, regex=True)
    assert [m["groups"]["total"] for m in r["result"]["matches"]] == ["1,906.05"]


def test_the_stream_order_would_not_have_matched(tmp_path):
    """The control: the same regex over the stream text finds nothing, so the test above tests the reorder."""
    import re

    doc = open_source(str(_drawn_values_first(tmp_path)))
    assert not re.search(TOTAL, load_page(doc, 1).text)


def test_the_invoice_it_was_found_on():
    r = read.find(str(real.path("superstore_invoice")), TOTAL, regex=True)
    assert [m["groups"]["total"] for m in r["result"]["matches"]] == ["1,906.05"]


def test_a_page_with_rotated_text_is_searched_as_drawn():
    """Pages 180-181 of the filing climb, and set "Jumlah ekuitas/ Total equity" at 270 degrees.

    Sorted by height, the vertical header became one letter per line and a
    find() for "Jumlah ekuitas" lost both pages.
    """
    doc = open_source(str(real.path("hybrid_financial")))
    for number in (180, 181):
        assert search_text(doc, number) == load_page(doc, number).text


def test_a_page_that_reads_top_to_bottom_is_searched_as_before(tmp_path):
    for fixture in (build.born_digital(), build.two_column()):
        doc = open_source(str(fixture))
        for number in range(1, doc.page_count + 1):
            assert search_text(doc, number) == load_page(doc, number).text, (
                f"{fixture.name} page {number} was reordered"
            )


class TestEachPageIsSearchedInTheReadingThatMatches:
    """Re-reading by line split a word the drawn order kept whole on 12 of 498 re-read corpus pages.

    "Executive" on a CFR contents page, "Diluted" in a filing's EPS table. The
    scan now takes both readings of such a page and searches the one the
    pattern matches more of, so it can only ever find what the drawn order did,
    or more.
    """

    def test_the_drawn_order_wins_where_the_reorder_split_the_word(self):
        from core import scan

        total, _, _, chosen = scan.matches_in_readings(
            [("Executive order", "Exec utive order")], "Executive", 0, 10, False
        )
        assert (total, chosen) == (1, [0])

    def test_the_reading_order_wins_where_it_matches_more(self):
        from core import scan

        readings = [("$1,906.05 Total:", "Total: $1,906.05")]
        total, hits, _, chosen = scan.matches_in_readings(readings, TOTAL, 0, 10, True)
        assert (total, chosen, hits[0][3]) == (1, [1], {"total": "1,906.05"})

    def test_a_tie_keeps_the_drawn_order(self):
        from core import scan

        assert scan.matches_in_readings([("a b", "b a")], "a", 0, 10, False)[3] == [0]
