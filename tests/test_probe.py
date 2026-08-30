"""probe() against the corpus, and the contract every response must satisfy.

Tests import the engine directly and never start an MCP process (CLAUDE.md
§12). The corpus is built on demand rather than committed, so a fresh clone
runs these with no extra step.
"""

from __future__ import annotations

import json
import math
import warnings

import pytest

from servers.docs_read import engine
from tests.fixtures import build


@pytest.fixture(scope="session")
def corpus():
    """Build every fixture except the 500-page one, which has its own test."""
    return build.build_all(include_large=False)


def _strict_json(payload: dict) -> None:
    """Fail if the response is not readable by a non-Python JSON parser.

    NaN and Infinity are not JSON. Python's encoder writes them as bare tokens
    and its decoder accepts them again, so the problem is invisible from inside
    Python -- and every test here is Python. A sibling repo shipped
    "mean": Infinity for a year that way: it round-trips perfectly in Python
    and takes out the ENTIRE response for any JavaScript, Go or Rust client.
    """

    def reject(constant: str) -> None:
        raise AssertionError(f"response contains the non-JSON literal {constant}")

    json.loads(json.dumps(payload), parse_constant=reject)


def _contract(payload: dict, op: str) -> None:
    """Every response, success or failure, must satisfy this."""
    assert payload["op"] == op
    assert isinstance(payload["success"], bool)
    assert isinstance(payload["token_estimate"], int)
    assert isinstance(payload["progress"], list)
    if payload["success"]:
        assert "result" in payload
    else:
        assert payload["error"] and payload["hint"], "a failure needs both an error and a hint"
        assert payload["hint"] != payload["error"], "a hint that restates the error is not a hint"
    _strict_json(payload)


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_born_digital_is_fully_extractable(corpus):
    payload = engine.probe(str(corpus["born_digital"]))
    _contract(payload, "probe")
    result = payload["result"]
    assert result["pages"] == 3
    assert result["extractable"] == "full"
    assert result["scanned_pages"] == ""
    assert result["page_kinds"]["born_digital"] == 3


def test_page_geometry_is_reported_in_points(corpus):
    result = engine.probe(str(corpus["born_digital"]))["result"]
    assert result["page_size"] == [612.0, 792.0]  # US Letter


# --------------------------------------------------------------------------
# The point of the whole design: per page, never per document
# --------------------------------------------------------------------------


def test_a_hybrid_document_is_not_described_by_one_verdict(corpus):
    """Two digital pages and two without a text layer, in one file.

    A single scanned true/false for this document would be a lie about half of
    it. The response has to separate them AND hand back a selection string the
    caller can paste into ocr().
    """
    result = engine.probe(str(corpus["hybrid"]))["result"]
    assert result["extractable"] == "partial"
    assert result["digital_pages"] == "1-2"
    assert result["scanned_pages"] == "3-4"
    assert result["page_kinds"]["born_digital"] == 2


def test_scanned_pages_is_a_string_ocr_can_take(corpus):
    """probe's output must parse as a selection, or the workflow is broken."""
    from core.selection import parse_pages

    result = engine.probe(str(corpus["hybrid"]))["result"]
    assert parse_pages(result["scanned_pages"], result["pages"]) == [3, 4]


def test_one_stray_glyph_is_not_a_text_layer(corpus):
    """Page 4 of the hybrid carries a burned-in page number and nothing else.

    A "does this page have any text" check calls that born-digital and sends
    the caller looking for text that is not there. The first version of the
    reader did exactly that, and reported basis="text_layer" beside
    is_scanned=True -- two fields describing one page in contradictory terms.
    """
    result = engine.probe(str(corpus["hybrid"]))["result"]
    assert result["page_kinds"]["born_digital"] == 2, "the 1-glyph page must not count as digital"
    assert "4" in result["scanned_pages"]


# --------------------------------------------------------------------------
# Honesty about sampling
# --------------------------------------------------------------------------


def test_a_sampled_signal_says_it_was_sampled(corpus):
    """Ruling-line detection samples. Reporting a count without saying so is
    the confidently-wrong claim this repo exists to avoid."""
    result = engine.probe(str(build.large()))["result"]
    assert result["tables"]["sample_is_every_page"] is False
    assert result["tables"]["sampled_pages"] < result["pages"]


def test_a_small_document_is_not_sampled(corpus):
    result = engine.probe(str(corpus["ruled_table"]))["result"]
    assert result["tables"]["sample_is_every_page"] is True
    assert result["tables"]["pages_with_ruling_lines"] == 1


def test_an_unruled_table_has_no_ruling_lines(corpus):
    """The pair with the test above. A tool that reports the same thing for
    both has implemented a constant, not a measurement."""
    result = engine.probe(str(corpus["unruled_table"]))["result"]
    assert result["tables"]["pages_with_ruling_lines"] == 0


# --------------------------------------------------------------------------
# Failures, and whether the hint is worth following
# --------------------------------------------------------------------------


def test_an_encrypted_file_names_the_way_in(corpus):
    payload = engine.probe(str(corpus["encrypted"]))
    _contract(payload, "probe")
    assert payload["success"] is False
    assert "password" in payload["hint"]


def test_the_password_actually_opens_it(corpus):
    """Following the hint has to work. Seventeen sweep rounds read hints and
    none followed one; every time one was checked in passing it was wrong."""
    payload = engine.probe(str(corpus["encrypted"]), password="secret")
    _contract(payload, "probe")
    assert payload["success"] is True
    assert payload["result"]["encrypted"] is True


def test_a_damaged_file_is_pointed_at_repair(corpus):
    payload = engine.probe(str(corpus["damaged"]))
    _contract(payload, "probe")
    assert payload["success"] is False
    assert "repair" in payload["hint"]


def test_a_missing_file_does_not_blame_the_document(corpus):
    payload = engine.probe("/nowhere/absent.pdf")
    _contract(payload, "probe")
    assert payload["success"] is False
    assert "absent.pdf" in payload["error"]


# --------------------------------------------------------------------------
# Budgets
# --------------------------------------------------------------------------


def test_a_large_document_reports_why_you_cannot_have_it_all():
    result = engine.probe(str(build.large()))["result"]
    assert result["token_estimate_full"] > 100_000
    assert 0 < result["pages_that_fit_one_response"] < result["pages"]


def test_the_map_of_a_huge_document_is_small():
    """The entire justification for probe: a 500-page document described in a
    response an agent can afford to read."""
    payload = engine.probe(str(build.large()))
    assert payload["token_estimate"] < 400
    assert payload["result"]["token_estimate_full"] > 100_000


def test_constrained_mode_lowers_what_fits(monkeypatch):
    """Clear the variable before measuring the unconstrained baseline.

    CI runs the whole suite with MCP_CONSTRAINED_MODE=1 set in the job env, so
    a test that only sets it is comparing constrained against constrained and
    asserting one is smaller than itself. It passed locally and failed in
    constrained mode, which is the wrong way round for a test whose entire
    subject is constrained mode.
    """
    large = str(build.large())
    monkeypatch.delenv("MCP_CONSTRAINED_MODE", raising=False)
    normal = engine.probe(large)["result"]["pages_that_fit_one_response"]
    monkeypatch.setenv("MCP_CONSTRAINED_MODE", "1")
    tight = engine.probe(large)["result"]["pages_that_fit_one_response"]
    assert tight < normal


# --------------------------------------------------------------------------
# Things that must never reach a response
# --------------------------------------------------------------------------


def test_no_warning_is_raised_reading_a_document(corpus):
    """pypdfium2's get_text_range() emits a UserWarning about being redirected.

    Warning text has a way of ending up in a response field, which this
    server's contract forbids -- so the reader must use get_text_bounded().
    Turning warnings into errors is the only check that stays true when
    somebody swaps the call back.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        engine.probe(str(corpus["born_digital"]))


def test_no_internal_handle_leaks_into_the_response(corpus):
    """The open PDFium handle lives in Document.meta. An object repr in JSON is
    a heap address: non-deterministic, useless, and unparseable."""
    text = json.dumps(engine.probe(str(corpus["born_digital"])))
    assert "object at 0x" not in text
    assert "_handle" not in text
    assert "PdfDocument" not in text


def test_every_numeric_field_is_finite(corpus):
    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, float):
            assert math.isfinite(value), f"non-finite float in response: {value}"

    walk(engine.probe(str(corpus["hybrid"])))


def test_the_password_never_reaches_the_response(corpus):
    """A heap address in JSON is useless; a credential in one is worse.

    The password has to be threaded to BOTH readers -- pypdfium2 for the text
    layer and pdfplumber for the geometry -- which means it lives on the
    Document for the length of the call. The encrypted fixture caught it
    missing from the second reader; this catches it arriving in the output.
    """
    text = json.dumps(engine.probe(str(corpus["encrypted"]), password="secret"))
    assert "secret" not in text
    assert "_password" not in text
