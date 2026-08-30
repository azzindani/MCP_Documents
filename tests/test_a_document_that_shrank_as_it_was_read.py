"""probe reported a smaller document every time another tool read part of it.

`token_estimate_full` is the number whose entire job is telling a caller why
they must not ask for the whole document at once, and `pages_that_fit_one_
response` is the range it hands them instead. Both are computed from
`Page.char_count`, and `char_count` summed the spans of whatever blocks were
cached:

    probe()                        -> 171,634 tokens, 8 pages fit
    extract(pages='6-7'); probe()  -> 171,516 tokens
    (all 183 pages read); probe()  -> 149,410 tokens, 9 pages fit

Same file, same call, three answers. `load_page_words` REPLACES a page's line
blocks with one block per word, and a word block carries no spaces, so the
count falls by one character for every space on every page anybody has looked
at -- 12.9% of this filing. Always downward, which is the dangerous direction:
the caller is told the document is smaller than it is, and then told that one
more page fits than actually does.

This is the same fault, in the same class, that round 2 fixed for `Page.text`
and `Page.is_scanned` -- both of which carry a comment saying so. `char_count`
sits between them in the file and was left counting blocks. A fix that stops at
its own siblings is half a fix.
"""

from __future__ import annotations

import pytest

from core.readers import load_page, load_page_words, open_source
from servers.docs_read import engine as read
from tests.fixtures import build, real


@pytest.fixture(scope="module")
def corpus():
    return build.build_all(include_large=False)


class TestTheCountDoesNotDependOnWhoLookedFirst:
    def test_char_count_survives_a_word_level_read(self, corpus):
        """The property, directly. Words carry no spaces; the count must not care."""
        path = str(corpus["born_digital"])
        doc = open_source(path)
        before = load_page(doc, 1).char_count
        assert before > 0
        load_page_words(doc, 1)
        assert load_page(doc, 1).char_count == before

    def test_char_count_counts_the_spaces(self, corpus):
        """It is `len(text)`, so a page with spaces counts more than its words."""
        doc = open_source(str(corpus["born_digital"]))
        page = load_page(doc, 1)
        spans = sum(len(s.text) for b in page.blocks for s in b.spans)
        assert page.char_count == len(page.text)
        assert page.char_count >= spans

    def test_probe_reports_the_same_size_after_extract(self, corpus):
        path = str(corpus["born_digital"])
        first = read.probe(path)["result"]
        read.extract(path, pages="1")
        again = read.probe(path)["result"]
        assert again["token_estimate_full"] == first["token_estimate_full"]
        assert again["pages_that_fit_one_response"] == first["pages_that_fit_one_response"]

    def test_every_read_tool_leaves_probe_alone(self, corpus):
        """Not just extract. Any tool that reads geometry replaces the cache."""
        path = str(corpus["two_column"])
        first = read.probe(path)["result"]["token_estimate_full"]
        for call in (
            lambda: read.read_page(path, 1),
            lambda: read.to_markdown(path),
            lambda: read.extract_tables(path),
            lambda: read.find(path, "the"),
        ):
            call()
            assert read.probe(path)["result"]["token_estimate_full"] == first


class TestTheRealFilingItWasFoundOn:
    """The committed 183-page filing, where the drift was 12.9%."""

    @pytest.fixture(scope="class")
    def filing(self):
        return str(real.path("hybrid_financial"))

    def test_the_estimate_holds_after_a_two_page_extract(self, filing):
        first = read.probe(filing)["result"]
        read.extract(filing, pages="6-7")
        again = read.probe(filing)["result"]
        assert again["token_estimate_full"] == first["token_estimate_full"]

    def test_the_suggested_range_holds_too(self, filing):
        """The count moved `pages_that_fit_one_response` from 8 to 9.

        A range that grows as the caller reads is worse than one that is merely
        wrong: it grows past what fits, in a response sized for what fitted.
        """
        first = read.probe(filing)["result"]["pages_that_fit_one_response"]
        read.read_page(filing, 6)
        read.extract_tables(filing, pages="6")
        assert read.probe(filing)["result"]["pages_that_fit_one_response"] == first
