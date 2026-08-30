"""find, extract, extract_tables, read_page, to_markdown, outline.

Two corpora. The synthetic one proves a specific thing on purpose; the real one
(tests/fixtures/real.py, skipped when absent) proves the things nobody thought
to build. Every defect fixed while writing these tools came from the second.
"""

from __future__ import annotations

import json

import pytest

from servers.docs_read import engine
from tests.fixtures import build
from tests.fixtures import real as realdocs


@pytest.fixture(scope="session")
def corpus():
    return build.build_all(include_large=False)


def _contract(payload: dict, op: str) -> None:
    assert payload["op"] == op
    assert isinstance(payload["token_estimate"], int)
    assert isinstance(payload["progress"], list)
    if not payload["success"]:
        assert payload["error"] and payload["hint"] and payload["hint"] != payload["error"]

    def reject(constant: str) -> None:
        raise AssertionError(f"non-JSON literal {constant} in the response")

    json.loads(json.dumps(payload), parse_constant=reject)


# --------------------------------------------------------------------------
# find -- locations, never content
# --------------------------------------------------------------------------


def test_find_returns_locations_not_the_document(corpus):
    payload = engine.find(str(corpus["running_heads"]), "covenant")
    _contract(payload, "find")
    result = payload["result"]
    assert result["hits"] > 0
    assert result["pages"]
    # The guard that keeps this tool cheap: a snippet, not a page.
    for match in result["matches"]:
        assert len(match["snippet"]) < 300


def test_find_named_groups_are_the_custom_parsing_path(corpus):
    """One pattern over a document returns ROWS, which is what a data server
    loads. Without named groups this tool is only a search."""
    payload = engine.find(str(corpus["ruled_table"]), r"(?P<item>Widget|Gadget)\s+(?P<qty>\d+)", regex=True)
    groups = [m["groups"] for m in payload["result"]["matches"] if "groups" in m]
    assert groups, "named groups must reach the response"
    assert {g["item"] for g in groups} <= {"Widget", "Gadget"}


def test_a_broken_regex_is_not_a_crash(corpus):
    payload = engine.find(str(corpus["born_digital"]), "(unclosed", regex=True)
    _contract(payload, "find")
    assert payload["success"] is False
    assert "regex=False" in payload["hint"]


def test_find_says_which_pages_it_could_not_search(corpus):
    """The hybrid's pages 3-4 have no text layer. Silently searching 2 of 4
    pages and reporting 0 hits is a wrong answer that looks like a right one."""
    payload = engine.find(str(corpus["hybrid"]), "Digital")
    assert payload["result"]["pages_skipped_no_text"] == 2


def test_a_truncated_find_says_so(corpus):
    payload = engine.find(str(corpus["running_heads"]), "the", max_hits=3)
    result = payload["result"]
    assert result["returned"] <= 3
    if result["hits"] > result["returned"]:
        assert result["truncated"] is True
        assert "max_hits" in result["hint"]


# --------------------------------------------------------------------------
# extract -- bounded by construction
# --------------------------------------------------------------------------


def test_extract_refuses_a_whole_large_document_with_a_range(corpus):
    payload = engine.extract(str(build.large()))
    _contract(payload, "extract")
    assert payload["success"] is False
    assert payload["refused"] == "budget"
    # The refusal has to hand back something usable, not just say no.
    assert "pages=" in payload["hint"]


def test_the_refused_range_actually_works(corpus):
    """Following the hint must succeed. Every time a hint was checked in
    passing across seventeen sweep rounds it turned out to be wrong."""
    refusal = engine.extract(str(build.large()))
    suggested = refusal["hint"].split("pages='")[1].split("'")[0]
    payload = engine.extract(str(build.large()), pages=suggested)
    assert payload["success"] is True
    assert payload["token_estimate"] < 12000


def test_running_heads_are_removed_and_the_body_is_not(corpus, monkeypatch):
    # This test is about CLEANING, so pin the budget rather than inherit it.
    # Under MCP_CONSTRAINED_MODE=1 the ceiling is 2,000 tokens and this fixture
    # is ~2,025, so the call was correctly refused and the assertion read that
    # as a cleaning failure. A test coupled to a limit it does not name fails
    # for a reason it cannot report -- and CI runs the whole suite constrained.
    monkeypatch.setenv("DOCS_MAX_RESPONSE_TOKENS", "50000")
    payload = engine.extract(str(corpus["running_heads"]))
    text = payload["result"]["text"]
    assert "CONFIDENTIAL" not in text
    assert "Acme Annual Report" not in text
    # 6 pages x 28 body lines. Losing any of them is a silent corruption
    # reported as success -- which is what the first version did, all 168.
    assert text.count("was reviewed by the board") == 168
    assert payload["result"]["cleaned"]["running_heads_removed"] == 12


def test_two_pages_cannot_detect_furniture_and_say_so(corpus):
    """A count of 0 beside a header that is plainly still there is a claim the
    numbers do not support."""
    payload = engine.extract(str(corpus["running_heads"]), pages="1-2")
    cleaned = payload["result"]["cleaned"]
    assert cleaned["running_heads_checked"] is False
    assert "at least" in cleaned["running_heads_note"]


def test_hyphens_join_across_a_page_break(corpus):
    payload = engine.extract(str(corpus["hyphenated"]), pages="1-2")
    text = payload["result"]["text"]
    assert "manufacturing" in text
    assert "reconsider" in text
    assert "state-of-the-art" in text, "a real hyphen must survive"


def test_a_two_column_page_is_not_read_across(corpus):
    payload = engine.extract(str(corpus["two_column"]), pages="1")
    text = payload["result"]["text"]
    assert payload["result"]["columns"] == [2]
    # Read across, line 1 would be "LEFT p1 line 1 ... RIGHT p1 line 1 ...".
    # The left column must be read out before the right begins.
    assert text.index("LEFT p1 line 2") < text.index("RIGHT p1 line 1")


def test_extract_names_the_pages_with_no_text(corpus):
    payload = engine.extract(str(corpus["hybrid"]))
    assert payload["result"]["pages_without_text"] == "3-4"


# --------------------------------------------------------------------------
# extract_tables -- a guess must not look like a measurement
# --------------------------------------------------------------------------


def test_a_ruled_table_and_a_guessed_one_are_not_equally_trusted(corpus):
    ruled = engine.extract_tables(str(corpus["ruled_table"]))["result"]["tables"][0]
    guessed = engine.extract_tables(str(corpus["unruled_table"]))["result"]["tables"][0]
    assert ruled["basis"] == "ruled"
    assert guessed["basis"] == "whitespace"
    assert ruled["confidence"] > guessed["confidence"]
    assert "verify" in guessed["note"]


def test_the_ruled_table_values_are_exact(corpus):
    rows = engine.extract_tables(str(corpus["ruled_table"]))["result"]["tables"][0]["rows"]
    assert rows[0] == ["Item", "Qty", "Price"]
    assert ["Sprocket", "2", "44.25"] in rows


def test_min_confidence_filters_the_guesses(corpus):
    payload = engine.extract_tables(str(corpus["unruled_table"]), min_confidence=0.9)
    assert payload["result"]["count"] == 0
    assert payload["result"]["found_before_filter"] >= 1


def test_a_table_sweep_of_a_huge_document_is_refused():
    payload = engine.extract_tables(str(build.large()))
    _contract(payload, "extract_tables")
    assert payload["success"] is False
    assert payload["refused"] == "budget"


# --------------------------------------------------------------------------
# read_page and to_markdown
# --------------------------------------------------------------------------


def test_read_page_finds_a_table_on_a_prose_page(corpus):
    payload = engine.read_page(str(corpus["ruled_table"]), 1)
    _contract(payload, "read_page")
    assert payload["result"]["tables"], "read_page is the go-and-look tool"


def test_a_page_that_does_not_exist_names_the_range(corpus):
    payload = engine.read_page(str(corpus["born_digital"]), 99)
    assert payload["success"] is False
    assert "1-3" in payload["hint"]


def test_to_markdown_refuses_a_document_that_does_not_fit():
    payload = engine.to_markdown(str(build.large()))
    assert payload["success"] is False
    assert "pages=" in payload["hint"]


def test_inferred_headings_are_labelled_as_inferred(corpus, monkeypatch):
    """Marking headings from type size is a guess. A response that presents it
    as structure the document declared is the defect."""
    monkeypatch.setenv("DOCS_MAX_RESPONSE_TOKENS", "50000")
    payload = engine.to_markdown(str(corpus["running_heads"]))
    assert payload["basis"] in {"text_layer", "font_size"}
    if "#" in payload["result"]["markdown"]:
        assert payload["basis"] == "font_size"


# --------------------------------------------------------------------------
# outline
# --------------------------------------------------------------------------


def test_a_document_with_no_headings_points_at_find(corpus):
    payload = engine.outline(str(corpus["born_digital"]))
    _contract(payload, "outline")
    assert payload["result"]["count"] == 0
    assert "find()" in payload["hint"]


# --------------------------------------------------------------------------
# Real documents. Skipped when the corpus is absent, so CI stays green.
# --------------------------------------------------------------------------


@realdocs.requires_real
def test_a_scanned_document_is_not_reported_as_empty():
    """An invoice that is a photograph. Zero characters is the right answer and
    "success, here is nothing" is the wrong way to give it."""
    payload = engine.probe(str(realdocs.path("scanned_invoice")))
    assert payload["result"]["extractable"] == "none"
    assert payload["result"]["scanned_pages"] == "1"


@realdocs.requires_real
def test_a_real_two_column_regulation_is_not_read_across():
    """The CFR volumes carry a full-width running head over two columns, which
    defeated the first two column detectors. Reading across produces
    'has sections connected by screws or In gas/vapor service means that a'."""
    payload = engine.extract(str(realdocs.path("huge_regulation")), pages="400")
    assert payload["result"]["columns"] == [2]
    assert "screws or In gas" not in payload["result"]["text"]


@realdocs.requires_real
def test_bookmarks_are_used_when_a_document_has_them():
    payload = engine.outline(str(realdocs.path("hybrid_financial")))
    assert payload["basis"] == "tagged"
    assert payload["result"]["count"] > 0
    assert all(entry["page"] is None or entry["page"] >= 1 for entry in payload["result"]["entries"])


@realdocs.requires_real
def test_an_843_page_document_is_mapped_in_a_readable_response():
    payload = engine.probe(str(realdocs.path("huge_regulation")))
    assert payload["result"]["pages"] > 800
    assert payload["result"]["token_estimate_full"] > 500_000
    assert payload["token_estimate"] < 400, "the map must fit where the document cannot"
