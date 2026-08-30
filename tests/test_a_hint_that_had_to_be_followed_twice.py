"""A budget refusal named a page range that its own estimator then refused.

Found by round 1's sweep, on a 238-page regulation:

    extract(source)             -> refused, "Use extract(pages='1-23')"
    extract(source, '1-23')     -> refused, "Use extract(pages='1-20')"
    extract(source, '1-20')     -> success

`refuse` exists precisely because the caller did nothing wrong and the hint
carries a value they can use. A value that does not work is worse than no
value: it costs a round trip and teaches the caller not to trust the next one.

The suggestion was `len(wanted) * ceiling / estimated` — the whole range's cost
spread evenly, then the FRONT of the range taken. The front of a document is
denser than its mean, always: front matter, a contents page, a dense preamble.
Measured on that regulation, mean 1,358 characters a page against 1,522 over
the first 23, so 23 pages is 35,027 characters against a 32,000 budget.

Now the candidate is measured with the SAME estimator that will judge the
caller's next call, and shrunk until that estimator accepts it. The hint is
true by the only definition that matters — the one the tool will apply.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from servers.docs_read import engine as read
from tests.fixtures import build

RANGE = re.compile(r"pages='([^']+)'")


@pytest.fixture(scope="session")
def front_heavy() -> Path:
    """First 10 pages ~4x denser than the last 30 — i.e. a real document."""
    return build.front_heavy()


def follow_the_hint(tool, source: str, limit: int = 6) -> list[str]:
    """Do exactly what each refusal says, and record the chain."""
    chain: list[str] = []
    payload = tool(source)
    for _ in range(limit):
        if payload["success"]:
            chain.append("OK")
            return chain
        found = RANGE.search(payload["hint"])
        assert found, f"a budget refusal with no range to follow: {payload['hint']}"
        chain.append(found.group(1))
        payload = tool(source, pages=found.group(1))
    chain.append("never succeeded")
    return chain


class TestTheFixtureHasThePropertyItExistsFor:
    def test_the_front_is_denser_than_the_mean(self, front_heavy):
        """A uniform document cannot show this defect: mean and front agree."""
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(front_heavy))
        counts = [len(document[i].get_textpage().get_text_bounded()) for i in range(len(document))]
        document.close()
        mean = sum(counts) / len(counts)
        assert sum(counts[:10]) / 10 > mean * 1.5

    def test_the_whole_document_is_refused(self, front_heavy):
        assert read.extract(str(front_heavy))["success"] is False


class TestOneFollowUpIsEnough:
    def test_extract(self, front_heavy):
        assert follow_the_hint(read.extract, str(front_heavy))[-1] == "OK"
        assert len(follow_the_hint(read.extract, str(front_heavy))) == 2

    def test_to_markdown(self, front_heavy):
        assert len(follow_the_hint(read.to_markdown, str(front_heavy))) == 2

    def test_the_suggested_range_is_not_empty(self, front_heavy):
        payload = read.extract(str(front_heavy))
        assert RANGE.search(payload["hint"]).group(1)

    def test_it_is_still_a_budget_refusal_not_a_failure(self, front_heavy):
        """`refused: budget` is what tells a caller they did nothing wrong."""
        payload = read.extract(str(front_heavy))
        assert payload["refused"] == "budget"
        assert payload["limit"] and payload["seen"]


class TestTheOrdinaryCasesAreUnchanged:
    def test_a_document_that_fits_is_not_refused(self):
        corpus = build.build_all(include_large=False)
        assert read.extract(str(corpus["born_digital"]))["success"]

    def test_a_large_uniform_document_still_refuses_with_a_working_range(self):
        """The 500-page fixture, where the mean was always right anyway."""
        assert follow_the_hint(read.extract, str(build.large()))[-1] == "OK"

    def test_a_caller_supplied_range_that_is_too_big_still_shrinks(self):
        chain = []
        payload = read.extract(str(build.large()), pages="1-400")
        assert payload["success"] is False
        chain.append(RANGE.search(payload["hint"]).group(1))
        assert read.extract(str(build.large()), pages=chain[0])["success"]
