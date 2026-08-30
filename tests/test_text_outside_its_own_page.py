"""Five tools raised IndexError on 12 of 170 real documents.

Found by round 1's sweep, running the read tier over the corpus rather than
over fixtures. Every Indonesian supreme-court judgment in it is 595.3pt wide
and carries a rotated margin watermark whose text runs out to 645.8 — text
outside the page's own MediaBox, which nothing forbids and which no fixture
here produced.

`core.order.gutter_positions` sized its crossings array to `int(page.width) + 1`
and then scanned `range(left, right + 1)` where `right` comes from the BLOCKS.
Writing into the array was clamped with `min(width, ...)`; reading out of it
was not. So `crossings[645]` on an array of 596.

It reached the caller as an unhandled exception, not a response: the MCP layer
returned `isError: true` with the string "Error executing tool read_page: list
index out of range" — no `success`, no `error`, no `hint`, no `token_estimate`.
This repo's contract is that every failure is a dict carrying all four, and
that no exception escapes an engine module.

Five of thirteen tools go through `detect_columns`: outline, extract,
read_page, to_markdown and convert. `probe` and `find` do not — so a caller
following the documented probe -> find -> extract path was told the document
was fine twice and then handed a Python traceback on the third call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import order
from core.ir import Block, Page, Span
from core.readers import load_page_words, open_source
from servers.docs_edit import engine as edit
from servers.docs_read import engine as read
from tests.fixtures import build


@pytest.fixture(scope="session")
def overflowing() -> Path:
    return build.overflowing()


class TestTheFixtureHasThePropertyItExistsFor:
    def test_text_really_does_extend_past_the_page_width(self, overflowing):
        """Otherwise every assertion below passes against the broken code."""
        doc = open_source(str(overflowing))
        page = load_page_words(doc, 1)
        right = max(b.bbox[2] for b in page.blocks if b.bbox)
        assert right > page.width, f"rightmost block {right} is inside the {page.width}pt page"


class TestNoToolRaises:
    """Each of the five that calls detect_columns, on the same document."""

    def test_read_page(self, overflowing):
        assert read.read_page(str(overflowing), 1)["success"]

    def test_outline(self, overflowing):
        assert read.outline(str(overflowing))["success"]

    def test_extract(self, overflowing):
        assert read.extract(str(overflowing))["success"]

    def test_to_markdown(self, overflowing):
        assert read.to_markdown(str(overflowing))["success"]

    def test_convert(self, overflowing, tmp_path):
        assert edit.convert(str(overflowing), "md", str(tmp_path / "out.md"))["success"]

    def test_the_overflowing_text_is_not_silently_dropped(self, overflowing):
        """Surviving the scan is not enough; the words still have to come back."""
        payload = read.extract(str(overflowing))
        assert "MARGIN WATERMARK TEXT" in payload["result"]["text"]
        assert "Body line 1" in payload["result"]["text"]


class TestGutterPositionsDirectly:
    """The unit, isolated from the five tools that were taking it down."""

    def _page(self, width: float, right_edge: float) -> Page:
        blocks = []
        for row in range(12):
            y = 100.0 + row * 14
            blocks.append(Block(kind="para", spans=[Span(text="left")], bbox=(50.0, y, 120.0, y + 10)))
            blocks.append(Block(kind="para", spans=[Span(text="over")], bbox=(right_edge - 40, y, right_edge, y + 10)))
        return Page(number=1, width=width, height=792.0, blocks=blocks)

    def test_a_block_past_the_right_edge_does_not_raise(self):
        order.gutter_positions(self._page(width=595.0, right_edge=645.0))

    def test_detect_columns_does_not_raise_either(self):
        assert order.detect_columns(self._page(width=595.0, right_edge=645.0)) >= 1

    def test_a_block_far_past_the_edge_still_does_not_raise(self):
        """The clamp has to be on the real extent, not on a fixed slack."""
        order.gutter_positions(self._page(width=200.0, right_edge=5000.0))

    def test_column_detection_on_a_real_two_column_page_is_unchanged(self):
        """The guard that matters: the fixture built to have two columns.

        Asserted against that fixture rather than against a hand-made Page.
        The first version of this test built twelve rows of two blocks and
        expected 2, which detect_columns correctly answers 1 — the gutter
        heuristics need more lines than that before they will commit. A
        regression test whose expectation is wrong is worse than none: it
        would have been "fixed" by loosening the detector.
        """
        corpus = build.build_all(include_large=False)
        doc = open_source(str(corpus["two_column"]))
        assert order.detect_columns(load_page_words(doc, 1)) == 2
