"""convert(to='images') rendered a page at whatever the memory budget allowed.

`budget.dpi_that_fits()` answers "what is the largest render that stays inside
the budget". `convert` used that answer as the resolution, so the resolution
became a property of the BATCH rather than of the page:

    1 page      600 DPI    33.7 MPx     5100 x 6601
    4 pages     423 DPI    16.7 MPx     3596 x 4653   <- the SAME page
    20 pages    189 DPI     3.3 MPx
    183 pages    62 DPI                 refused

Confirmed against the deployment, not computed: page 6 of the bank filing came
back at two different resolutions depending only on how many neighbours were
asked for in the same call. Four pages came to 8.8 MB of PNG.

The low end was already guarded -- it refuses under 72 DPI. The high end was
not, and there was no default anywhere: nothing told a caller what resolution
to expect. The sibling shows the standard this repo already holds itself to.
`ocr()` renders at a fixed `DOCS_OCR_DPI=200` with its measurement written in a
comment beside it. One tool picked its DPI by measurement and the other by
leftover memory.

The refusal was its own defect:

    "hint": "Render fewer pages, or accept 62 DPI by rendering a range with read_page()."

`read_page()` renders nothing -- it returns text, tables and links, and has no
DPI. And `convert` has no `pages` parameter, so "render fewer pages" was not
available through the tool that had just refused either. Both halves were
impossible; the caller has to `assemble` a range first, which the hint never
mentioned. Hints are assertions and have to be tested like any other.
"""

from __future__ import annotations

import pytest
from PIL import Image

from core import budget
from servers.docs_edit import engine as edit
from tests.fixtures import build


@pytest.fixture(scope="module")
def corpus():
    return build.build_all(include_large=True)


def sizes(directory):
    return [Image.open(p).size for p in sorted(directory.glob("page_*.png"))]


class TestThePageRendersTheSameWhoeverItIsWith:
    def test_one_page_and_three_pages_agree(self, corpus, tmp_path):
        one = edit.convert(str(build.born_digital(pages=1, name="just_one.pdf")), to="images", out=str(tmp_path / "a"))
        many = edit.convert(str(corpus["born_digital"]), to="images", out=str(tmp_path / "b"))
        assert one["success"] and many["success"], (one, many)
        assert one["result"]["dpi"] == many["result"]["dpi"]
        assert sizes(tmp_path / "a")[0] == sizes(tmp_path / "b")[0]

    def test_the_default_is_the_measured_one(self, corpus, tmp_path):
        payload = edit.convert(str(corpus["born_digital"]), to="images", out=str(tmp_path / "pages"))
        assert payload["result"]["dpi"] == budget.render_dpi()

    # Sized so the ceiling lands BETWEEN the 72 DPI floor and the 150 default:
    # three US Letter pages at 4 bytes a pixel is about 3.7 MB per page at 100
    # DPI, so 12 MB puts the ceiling near 105. At 4 MB it refuses instead,
    # which is a different branch and not what these two are about.
    LOWERED_BUDGET = str(12 * 1024 * 1024)

    def test_the_budget_can_only_lower_it(self, corpus, tmp_path, monkeypatch):
        """A ceiling, not a setting. Below the default it wins; above it does nothing."""
        monkeypatch.setenv("DOCS_MAX_RENDER_BYTES", self.LOWERED_BUDGET)
        payload = edit.convert(str(corpus["born_digital"]), to="images", out=str(tmp_path / "small"))
        assert payload["success"], payload
        assert 72 <= payload["result"]["dpi"] < budget.render_dpi()

    def test_and_says_so_when_it_does(self, corpus, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCS_MAX_RENDER_BYTES", self.LOWERED_BUDGET)
        payload = edit.convert(str(corpus["born_digital"]), to="images", out=str(tmp_path / "small"))
        messages = " ".join(str(step) for step in payload["progress"])
        assert "rather than" in messages and str(budget.render_dpi()) in messages

    def test_a_higher_budget_does_not_raise_the_resolution(self, corpus, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCS_MAX_RENDER_BYTES", str(4 * 1024 * 1024 * 1024))
        payload = edit.convert(str(corpus["born_digital"]), to="images", out=str(tmp_path / "big"))
        assert payload["result"]["dpi"] == budget.render_dpi()


class TestTheRefusalNamesSomethingThatCanBeDone:
    @pytest.fixture(scope="class")
    def refusal(self, corpus, tmp_path_factory):
        payload = edit.convert(str(corpus["large"]), to="images", out=str(tmp_path_factory.mktemp("x") / "p"))
        assert not payload["success"], payload
        return payload

    def test_it_refuses_rather_than_rendering_unreadably(self, refusal):
        assert refusal["refused"] == "budget"

    def test_it_does_not_send_the_caller_to_a_tool_that_renders_nothing(self, refusal):
        assert "read_page(" not in refusal["hint"]

    def test_it_names_the_tool_that_can_take_a_range(self, refusal):
        assert "assemble(" in refusal["hint"]
        assert "convert(" in refusal["hint"]

    def test_it_says_how_many_pages_fit(self, refusal):
        fits = budget.pages_that_fit_render(612.0, 792.0, budget.render_dpi())
        assert str(fits) in refusal["hint"]
        assert fits >= 1


class TestTheBudgetHelper:
    def test_it_counts_whole_pages_only(self):
        assert budget.pages_that_fit_render(612.0, 792.0, budget.render_dpi()) >= 1

    def test_a_bigger_page_fits_fewer_of_itself(self):
        letter = budget.pages_that_fit_render(612.0, 792.0, 150)
        poster = budget.pages_that_fit_render(1224.0, 1584.0, 150)
        assert poster < letter

    def test_doubling_the_dpi_quarters_the_count(self):
        """Whole pages, so the answer is the floor of a quarter, not a quarter."""
        low = budget.pages_that_fit_render(612.0, 792.0, 100)
        high = budget.pages_that_fit_render(612.0, 792.0, 200)
        assert high == low // 4 or high == low // 4 + 1
