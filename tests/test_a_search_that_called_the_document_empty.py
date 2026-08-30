"""Two tools reported `basis: "empty"` about documents that are not empty.

`empty` is the one basis in the vocabulary worth 0.0 confidence, and `core/ir`
defines it as "nothing here to obtain". Both of these said it about a page they
had just read successfully:

  * `find` returned `basis: "empty"` whenever nothing MATCHED -- so a fruitless
    search of the 183-page born-digital filing described the document the way
    it describes a blank page, in the same session in which `probe` reported
    182 born-digital pages and a full text layer.

  * `extract_tables` returned `basis: "empty"` whenever `min_confidence`
    filtered every table out -- in the same response object as
    `found_before_filter: 1`, about a page whose table it had just read at
    confidence 0.95. Two fields, one object, flatly contradicting each other.

Both are the same mistake: describing the OUTCOME of a query with a field that
describes how the CONTENT was obtained. Finding nothing is a fact about the
query. A caller filtering tables out is a fact about the caller.

A negative answer looks like a clean pass, which is why neither survived being
looked at directly and neither had a test.
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


class TestFindingNothingSaysNothingAboutTheDocument:
    def test_a_miss_keeps_the_basis_of_the_pages_searched(self, corpus):
        path = str(corpus["born_digital"])
        hit = read.find(path, "the")
        miss = read.find(path, "Wakanda Interbank Settlement")
        assert hit["result"]["hits"] > 0 and miss["result"]["hits"] == 0
        assert miss["basis"] == hit["basis"] == "text_layer"

    def test_it_does_not_contradict_probe_on_the_same_file(self, corpus):
        path = str(corpus["born_digital"])
        assert read.probe(path)["result"]["extractable"] == "full"
        assert read.find(path, "nothing-like-this-is-here")["basis"] != "empty"

    def test_the_real_filing_too(self):
        filing = str(real.path("hybrid_financial"))
        assert read.find(filing, "Wakanda Interbank Settlement")["result"]["hits"] == 0
        assert read.find(filing, "Wakanda Interbank Settlement")["basis"] == "text_layer"

    def test_a_document_with_no_text_at_all_still_says_empty(self, corpus):
        """The fallback has to keep working, or this trades one lie for another."""
        payload = read.find(str(corpus["blank_and_scanned"]), "anything", pages="2-3")
        assert payload["result"]["hits"] == 0
        assert payload["basis"] == "empty"

    def test_a_format_that_declares_its_structure_keeps_saying_so(self, formats):
        payload = read.find(str(formats["article_html"]), "no-such-string-anywhere")
        assert payload["result"]["hits"] == 0
        assert payload["basis"] == "native"


class TestFilteringTablesOutSaysNothingAboutThePage:
    def test_a_filter_that_removes_everything_keeps_the_basis(self, corpus):
        path = str(corpus["ruled_table"])
        payload = read.extract_tables(path, min_confidence=0.99)
        assert payload["result"]["count"] == 0
        assert payload["result"]["found_before_filter"] == 1
        assert payload["basis"] == "ruled", "the table it just read is ruled, not absent"

    def test_the_basis_matches_the_unfiltered_call(self, corpus):
        for name in ("ruled_table", "unruled_table"):
            path = str(corpus[name])
            assert read.extract_tables(path, min_confidence=0.99)["basis"] == read.extract_tables(path)["basis"]

    def test_no_table_at_all_still_says_empty(self, formats):
        """A format with no tables is a different answer from a filtered one."""
        payload = read.extract_tables(str(formats["plain_txt"]))
        assert payload["result"]["count"] == 0
        assert payload["result"]["found_before_filter"] == 0
        assert payload["basis"] == "empty"

    def test_the_response_never_contradicts_itself(self, corpus):
        """The general form: 0.0 confidence beside a positive finding."""
        payload = read.extract_tables(str(corpus["ruled_table"]), min_confidence=0.99)
        found = payload["result"]["found_before_filter"]
        assert not (found > 0 and payload["basis"] == "empty")
