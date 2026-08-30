"""`read_page` returned 259.358.79331 as a bank's total equity. It is 259.358.793.

Found by sweeping the read tier against a real IDX filing -- 183 pages, three
page geometries, four of them landscape. On the consolidated statement of
changes in equity, the response was `success: true`, `basis: "text_layer"`, the
highest-confidence basis this server has, and:

    ... 259.132.407 226.386 259.358.79331 March 2026

The PDF's own text layer has it right: `259.358.793\\r\\nBalance,\\r\\n31 March 2026`.
The corruption is introduced on the way out. pdfplumber segments words by
horizontal gap with a 3pt tolerance, and on a bilingual statement the English
date label of the row sits 1.92pt from the last digit of the Indonesian column,
so the two are returned as one token.

Nine other tokens on four other pages are glued the same way
(`8.450.933receivables`, `208.351Unaccepted`). Those are recoverable -- a reader
sees the letters and knows where the number stops. This one is not. It is a
plausible twelve-digit figure that does not appear anywhere in the document,
handed over as a fact, and it is the single number the statement exists to
report.

**Why the obvious fix is worse than the defect.** Lowering pdfplumber's word
tolerance is one argument and it was measured across the corpus before being
rejected: a negative figure's own closing bracket sits 1.26pt from its last
digit in one filing and 2.58pt in another -- FURTHER than the glue this was
meant to catch. A smaller tolerance strips the bracket off `(90.901.000)` and
turns a loss into a gain. The first two versions of the rule, both plausible,
also truncated `1.424.901.522` to `1.424.901` and `10.000,00` to `10.000,0`
across six other documents. Only measuring caught that.

So the fix splits on the SHAPE of the token -- a complete thousands-grouped
figure with a non-continuation tail -- and only where the page's own characters
confirm a real gap at that exact boundary. Measured over 32 real documents it
fires on ten tokens and nothing else.

The oracle here is not another of this server's tools. It is the XBRL instance
of the SAME filing, filed by the same issuer in the same submission and
produced by a different tool chain, which states the figure machine-readably.
"""

from __future__ import annotations

import pytest

from core.readers import load_page_words, open_source
from servers.docs_read import engine as read
from tests.fixtures import real


@pytest.fixture(scope="module")
def filing():
    return str(real.path("hybrid_financial"))


@pytest.fixture(scope="module")
def facts():
    return real.xbrl_facts()


# The page carrying the consolidated statement of changes in equity, and the
# page numbers of the other glued tokens. Named rather than searched, because a
# test that hunts for the defect will pass on a document that no longer has it.
EQUITY_PAGE = 10
GLUED_PAGES = {129: "208.351", 130: "28.649", 143: "8.450.933", 144: "9.494.630"}


class TestTheFigureIsTheOneTheIssuerFiled:
    def test_total_equity_matches_the_xbrl(self, filing, facts):
        """The number in the PDF is the number in the filing's own XBRL."""
        equity = facts["Equity|CurrentYearInstant"]
        assert equity == 259_358_793, "the oracle itself moved; check the fixture"

        result = read.read_page(filing, EQUITY_PAGE)
        assert result["success"] is True
        assert "259.358.793" in result["result"]["text"]

    def test_the_twelve_digit_number_is_gone(self, filing):
        """259.358.79331 is not a number; it must not be returned as one."""
        result = read.read_page(filing, EQUITY_PAGE)
        assert "259.358.79331" not in result["result"]["text"]

    def test_extract_agrees_with_read_page(self, filing):
        """Both tools read the page through the word path; both must be right.

        They disagreed with the PDF in the same way, which is why fixing one
        would have left the other quietly wrong.
        """
        result = read.extract(filing, pages=str(EQUITY_PAGE))
        assert result["success"] is True
        assert "259.358.79331" not in result["result"]["text"]
        assert "259.358.793" in result["result"]["text"]

    @pytest.mark.parametrize("page,figure", sorted(GLUED_PAGES.items()))
    def test_figures_are_not_glued_to_the_next_label(self, filing, page, figure):
        """The recoverable cases, which share one cause with the dangerous one."""
        words = [
            span.text
            for block in load_page_words(open_source(filing), page).blocks
            for span in block.spans
        ]
        assert figure in words
        assert not [w for w in words if w.startswith(figure) and w != figure]


class TestNothingElseWasSplit:
    """The half of this that matters: the fix must not invent a new defect."""

    def test_a_negative_figure_keeps_its_bracket(self, filing):
        """`(1.104.154)` must not become `1.104.154` -- that flips the sign.

        The gap before a closing bracket is larger than the glue gap in some
        filings, so this is the case a tolerance-based fix breaks.
        """
        words = [
            span.text
            for block in load_page_words(open_source(filing), EQUITY_PAGE).blocks
            for span in block.spans
        ]
        bracketed = [w for w in words if w.startswith("(") and w.endswith(")")]
        assert bracketed, "the page has negative figures; none survived as a unit"
        assert "(1.104.154)" in words

    @pytest.mark.parametrize(
        "text",
        [
            "1.424.901.522",  # a longer figure, split after 1.424.901 by version 1
            "10.000,00",  # a decimal figure, split after 10.000,0 by version 2
            "2.076.893.645.183,00",
            "(53.087.197.697)",
            "53.941.121)",  # a bracket that arrives on its own
        ],
    )
    def test_a_whole_figure_is_left_whole(self, text):
        """Tokens from other real documents that earlier rules truncated."""
        from core.readers.pdf import _GLUED_FIGURE

        match = _GLUED_FIGURE.match(text)
        split = bool(match) and match.group(2)[0] not in ")" and match.group(2)[0].isalnum()
        assert not split, f"{text!r} would be split apart"

    def test_a_page_whose_characters_do_not_reconstruct_is_untouched(self, filing):
        """The guard that keeps watermarked judgments out of this entirely.

        Every supreme-court judgment in the corpus carries a rotated margin
        watermark whose characters interleave with the body text, so a word's
        characters cannot be recovered from its own line. An earlier version of
        this rule read those pages and split `intelektual` into `intelektunal`.
        """
        from core.readers.pdf import _unglue

        class Unreconstructable:
            chars = [{"top": 1.0, "x0": 0.0, "x1": 5.0, "text": "?"}]

        word = {"text": "8.450.933receivables", "top": 1.0, "x0": 0.0, "x1": 90.0}
        assert _unglue(Unreconstructable(), [word]) == [word]
