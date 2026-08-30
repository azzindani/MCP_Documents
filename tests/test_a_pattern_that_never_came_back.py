"""find(regex=True) ran a caller's pattern with nothing bounding it.

`re` has no timeout and no step limit. A pattern with a quantifier inside a
quantifier -- `(\\s*\\w+)+$` is the textbook one -- backtracks super-
exponentially, and against ONE page of an ordinary financial filing it was
still running when it was killed at 120 seconds.

On a deployed HTTP server that is not a slow answer, it is a worker that never
comes back: the caller's client times out, the process keeps burning a core,
and nothing in the response, the log or the health check says why. The tool
advertises regex as a feature -- its own module docstring calls it "the custom
parsing path" -- so the pattern arrives from outside by design.

The guard runs the match in a child process the parent can kill. Three
measurements decided it, and they are in core/scan.py and core/budget.py:

  * the worst LEGITIMATE pattern over all 183 pages takes 0.324s, so a 10s
    ceiling is thirty times the real worst case and cannot be reached by
    anything that finishes;
  * the child costs ~10ms warm and ~78ms on the first call of a process,
    against the seconds `find` already spends reading the document;
  * a literal query has been through `re.escape`, holds no quantifier, and
    cannot backtrack -- so it stays in-process and pays nothing.
"""

from __future__ import annotations

import time

import pytest

from core import budget, scan
from servers.docs_read import engine as read
from tests.fixtures import build, real

# Cheap and unmistakable: nested quantifiers over a subject with no match at
# the end, which is the shape that forces the engine to try every partition.
EVIL = r"(\s*\w+)+$"
SUBJECT = ["word " * 60 + "!"]


@pytest.fixture(scope="module")
def corpus():
    return build.build_all(include_large=False)


@pytest.fixture(scope="module")
def dense_page():
    """The committed filing. The synthetic fixtures are ~350 characters a page,
    and `(\\s*\\w+)+$` finishes instantly on a subject that short -- so a test
    written against them would pass against the unguarded code too. Page 49 is
    a full page of accounting-policy prose, which is where this was found."""
    return str(real.path("hybrid_financial"))


class TestTheGuardStopsIt:
    def test_a_runaway_pattern_raises_rather_than_running(self):
        started = time.perf_counter()
        with pytest.raises(scan.ScanTimeout):
            scan.finditer_bounded(EVIL, 0, SUBJECT, ceiling=10, timeout=1.0)
        assert time.perf_counter() - started < 20, "the deadline did not stop it"

    def test_the_tool_refuses_instead_of_hanging(self, dense_page, monkeypatch):
        monkeypatch.setenv("DOCS_REGEX_SECONDS", "1")
        started = time.perf_counter()
        payload = read.find(dense_page, EVIL, regex=True, pages="49")
        assert time.perf_counter() - started < 20
        assert not payload["success"], payload
        assert payload["refused"] == "budget"

    def test_the_refusal_says_what_to_do_about_it(self, dense_page, monkeypatch):
        monkeypatch.setenv("DOCS_REGEX_SECONDS", "1")
        payload = read.find(dense_page, EVIL, regex=True, pages="49")
        assert "quantifier" in payload["hint"]
        assert "regex=False" in payload["hint"], "the way out has to be in the hint"
        assert payload["limit"] == "1s"


class TestTheGuardDoesNotChangeAnyAnswer:
    def test_a_guarded_scan_finds_what_an_unguarded_one_finds(self, corpus):
        texts = ["alpha beta gamma", "beta beta", "nothing here", ""]
        guarded = scan.matches_of(texts, r"beta", 0, 10, guarded=True)
        direct = scan.matches_of(texts, r"beta", 0, 10, guarded=False)
        assert guarded == direct
        assert guarded[0] == 3
        assert guarded[2] == [0, 1]

    def test_named_groups_survive_the_process_boundary(self):
        """The parsing path the module docstring advertises."""
        texts = ["Invoice INV-9001 total 1450.50"]
        total, hits, where = scan.matches_of(texts, r"(?P<ref>INV-\d+).*?(?P<amount>[\d.]+)$", 0, 10, guarded=True)
        assert total == 1 and where == [0]
        assert hits[0][3] == {"ref": "INV-9001", "amount": "1450.50"}

    def test_the_page_list_is_not_truncated_by_the_match_cap(self):
        """`where` is counted, never derived from a capped list of matches."""
        texts = [f"hit {n}" for n in range(20)]
        total, hits, where = scan.matches_of(texts, r"hit", 0, ceiling=3, guarded=True)
        assert total == 20
        assert len(hits) == 3
        assert where == list(range(20))

    def test_a_literal_query_is_unaffected_by_the_regex_budget(self, corpus, monkeypatch):
        """re.escape leaves no quantifier, so no child and no deadline."""
        monkeypatch.setenv("DOCS_REGEX_SECONDS", "0.001")
        payload = read.find(str(corpus["born_digital"]), "the")
        assert payload["success"], payload
        assert payload["result"]["hits"] > 0


class TestTheBudgetItself:
    def test_it_is_far_above_the_worst_real_pattern(self):
        """0.324s was the slowest of ten plausible patterns over 183 pages."""
        assert budget.regex_seconds() >= 3.0

    def test_constrained_mode_tightens_it(self, monkeypatch):
        monkeypatch.setenv("MCP_CONSTRAINED_MODE", "1")
        tight = budget.regex_seconds()
        monkeypatch.setenv("MCP_CONSTRAINED_MODE", "0")
        assert tight < budget.regex_seconds()

    def test_it_is_overridable(self, monkeypatch):
        monkeypatch.setenv("DOCS_REGEX_SECONDS", "2.5")
        assert budget.regex_seconds() == 2.5
