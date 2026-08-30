"""find() told the caller to raise max_hits when raising it changed nothing.

The number of matches returned is `min(max_hits, what the response budget
affords)` -- 400 with the default 8,000-token ceiling, 100 under
MCP_CONSTRAINED_MODE. Past that point `max_hits` is not the limit that bit, and
the hint said to raise it anyway:

    max_hits=500    hits=2360  returned=400  "... or raise max_hits."
    max_hits=5000   hits=2360  returned=400  "... or raise max_hits."
    max_hits=50000  hits=2360  returned=400  "... or raise max_hits."

A caller who follows a hint and gets a byte-identical response has been told
nothing, and has no way to find out which of the two limits they are against.
This is the same shape as round 1's budget refusal that named a range its own
estimator refused, and round 4's refusal that named a tool which renders no
images: read a hint as an assertion and test it.

`returned_limit` is now in the response as well, so the ceiling is a number the
caller can see rather than one they have to infer by bisection.
"""

from __future__ import annotations

import pytest

from core import budget
from servers.docs_read import engine as read
from tests.fixtures import real

QUERY = r"\d{1,3}\.\d{3}\.\d{3}"


@pytest.fixture(scope="module")
def filing():
    return str(real.path("hybrid_financial"))


@pytest.fixture(scope="module")
def ceiling():
    return budget.max_response_tokens() // 20


class TestTheHintNamesTheLimitThatActuallyBit:
    def test_below_the_ceiling_it_still_says_raise_max_hits(self, filing, ceiling):
        payload = read.find(filing, QUERY, regex=True, max_hits=20)["result"]
        assert payload["truncated"] is True
        assert payload["returned"] == 20
        assert "raise max_hits" in payload["hint"]

    def test_above_the_ceiling_it_does_not(self, filing, ceiling):
        payload = read.find(filing, QUERY, regex=True, max_hits=ceiling * 10)["result"]
        assert payload["returned"] == ceiling
        assert "raise max_hits" not in payload["hint"]

    def test_and_says_a_larger_max_hits_returns_no_more(self, filing, ceiling):
        payload = read.find(filing, QUERY, regex=True, max_hits=ceiling * 10)["result"]
        assert "returns no more" in payload["hint"]
        assert str(ceiling) in payload["hint"]

    def test_the_ceiling_is_a_field_not_just_prose(self, filing, ceiling):
        payload = read.find(filing, QUERY, regex=True, max_hits=ceiling * 10)["result"]
        assert payload["returned_limit"] == ceiling

    def test_following_the_hint_changes_the_answer_where_it_is_given(self, filing):
        """The test of a hint is that acting on it does something."""
        small = read.find(filing, QUERY, regex=True, max_hits=20)["result"]
        bigger = read.find(filing, QUERY, regex=True, max_hits=200)["result"]
        assert "raise max_hits" in small["hint"]
        assert bigger["returned"] > small["returned"]


class TestTheCountAndThePagesAreNeverTruncated:
    def test_the_total_is_the_same_whatever_the_cap(self, filing):
        totals = {read.find(filing, QUERY, regex=True, max_hits=n)["result"]["hits"] for n in (1, 50, 400)}
        assert len(totals) == 1

    def test_the_page_list_is_the_same_whatever_the_cap(self, filing):
        pages = {read.find(filing, QUERY, regex=True, max_hits=n)["result"]["pages"] for n in (1, 50, 400)}
        assert len(pages) == 1, "the locations are this tool's primary answer; a cap must not shrink them"

    def test_an_untruncated_answer_carries_no_ceiling_field(self, filing):
        payload = read.find(filing, "JUMLAH EKUITAS")["result"]
        assert payload["hits"] == payload["returned"]
        assert "truncated" not in payload
        assert "returned_limit" not in payload
