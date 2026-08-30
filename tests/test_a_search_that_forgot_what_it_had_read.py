"""`find` returned fewer hits the second time, because `extract` had run.

Found by sweeping the read tier against a real 183-page IDX filing. `find` was
asked for `JUMLAH EKUITAS` -- the line carrying the bank's total equity -- and
answered:

    {"hits": 0, "pages": "", "pages_searched": 183}

with `success: true`. The phrase is on pages 7 and 178. `find` had already
printed it, in its own snippet, in the reply to a different query:

    "…226.386 221.077 Non-controlling interest JUMLAH EKUITAS 259.358.793…"

The cause is cache state, not search. `load_page` builds one block per LINE, so
`Page.text` joined with "\\n" reproduces the page. `load_page_words` builds one
block per WORD and then REPLACES the cached page, because a page with geometry
is strictly more useful than one without. After that the same join puts a
newline where every space in the document was, and `re.escape("JUMLAH EKUITAS")`
-- which contains a literal space -- matches nothing.

So the answer depended on what had been called before it:

    find('JUMLAH EKUITAS')                      -> 5 hits, pages 7,178,180-181
    extract(pages='7'); find('JUMLAH EKUITAS')  -> 3 hits, pages 178,180-181

Same document, same call, same process. No warning, no change of basis, and the
pages that disappear are exactly the ones the caller has just been reading --
on `probe -> find -> extract`, the workflow this server documents and that
`probe`'s own hint recommends.

Single-word queries always worked, which is why nothing caught it: `EKUITAS`
returns 54 hits either way. It takes a space in the query and a page someone
already looked at.

Fixed by giving `Page` a `raw_text` set by the reader and never rebuilt from
blocks, so what a page says it contains stops depending on which reader touched
it last.
"""

from __future__ import annotations

import pytest

from core.readers import load_page, load_page_words, open_source
from servers.docs_read import engine as read
from tests.fixtures import real

# A phrase, not a word -- one word matches under either representation, and
# that is precisely why the defect survived every existing test.
PHRASE = "JUMLAH EKUITAS"
PHRASE_PAGES = "7,178,180-181"


@pytest.fixture(scope="module")
def filing():
    return str(real.path("hybrid_financial"))


class TestTheAnswerDoesNotDependOnWhatRanBefore:
    def test_find_is_the_same_before_and_after_extract(self, filing):
        first = read.find(filing, PHRASE)
        assert first["result"]["hits"] == 5
        assert first["result"]["pages"] == PHRASE_PAGES

        read.extract(filing, pages="7")

        again = read.find(filing, PHRASE)
        assert again["result"]["hits"] == first["result"]["hits"]
        assert again["result"]["pages"] == first["result"]["pages"]

    def test_every_geometry_tool_leaves_find_intact(self, filing):
        """read_page, to_markdown and extract all replace the cached page."""
        before = read.find(filing, PHRASE)["result"]
        read.read_page(filing, 7)
        read.to_markdown(filing, pages="178")
        read.extract(filing, pages="180-181")
        after = read.find(filing, PHRASE)["result"]
        assert after["hits"] == before["hits"]
        assert after["pages"] == before["pages"]

    def test_the_phrase_is_really_there(self, filing):
        """Against the document, not against another of this server's answers."""
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(filing)
        pages = [n for n in range(1, len(pdf) + 1) if PHRASE in pdf[n - 1].get_textpage().get_text_bounded()]
        assert pages == [7, 178]


class TestThePageKeepsItsOwnText:
    def test_word_reading_does_not_lose_the_spaces(self, filing):
        doc = open_source(filing)
        cheap = load_page(doc, 7)
        assert PHRASE in cheap.text

        worded = load_page_words(doc, 7)
        assert PHRASE in worded.text, "the page lost its spaces when it gained geometry"

    def test_a_page_with_no_reader_text_still_reads_its_blocks(self):
        """The fallback the flow formats depend on -- html, txt, email."""
        from core.ir import Block, Page, Span

        page = Page(number=1, blocks=[Block(kind="para", spans=[Span(text="hello world")])])
        assert page.raw_text == ""
        assert page.text == "hello world"
