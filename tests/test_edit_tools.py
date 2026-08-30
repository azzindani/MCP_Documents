"""assemble, convert, optimize, ocr, protect, redact.

Every write tool here is checked by OPENING WHAT IT WROTE, not by reading its
own success flag. That is the fleet's single highest-yield rule: `sort_sheet`
once returned `success: true, rows_sorted: 4` while sorting the header row into
the data, and nothing in the response was wrong.
"""

from __future__ import annotations

import json

import pikepdf
import pypdfium2 as pdfium
import pytest

from core import binaries
from servers.docs_edit import engine
from tests.fixtures import build
from tests.fixtures import real as realdocs


@pytest.fixture(scope="session")
def corpus():
    return build.build_all(include_large=False)


@pytest.fixture(autouse=True)
def _output_dir(tmp_path, monkeypatch):
    """Every test writes into its own directory.

    Not a shared one: these tools take an `out` name and default into
    MCP_OUTPUT_DIR, so two tests using the same name would silently read each
    other's artifacts and both pass.
    """
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path))
    return tmp_path


def _contract(payload: dict, op: str) -> None:
    assert payload["op"] == op
    assert isinstance(payload["token_estimate"], int)
    if not payload["success"]:
        assert payload["error"] and payload["hint"] and payload["hint"] != payload["error"]

    def reject(constant: str) -> None:
        raise AssertionError(f"non-JSON literal {constant} in the response")

    json.loads(json.dumps(payload), parse_constant=reject)


# --------------------------------------------------------------------------
# assemble -- six buttons, one grammar
# --------------------------------------------------------------------------


def test_merge_reorder_and_rotate_in_one_call(corpus, _output_dir):
    payload = engine.assemble(
        [str(corpus["born_digital"]), str(corpus["two_column"])],
        "born_digital:1-2, two_column:all, born_digital:3r90",
        "merged.pdf",
    )
    _contract(payload, "assemble")
    assert payload["result"]["pages_written"] == 5

    # Open the file. The claim is not the evidence.
    with pikepdf.open(payload["result"]["out"]) as written:
        assert len(written.pages) == 5
        assert int(written.pages[4].obj.get("/Rotate", 0)) == 90  # type: ignore[arg-type]


def test_the_parse_is_echoed_back(corpus, _output_dir):
    """A grammar whose interpretation cannot be inspected is the banned
    ops-dict wearing a different hat."""
    payload = engine.assemble([str(corpus["born_digital"])], "born_digital:3,1", "r.pdf")
    parsed = payload["result"]["parsed"]
    assert [clause["pages"] for clause in parsed] == [[3], [1]]


def test_reorder_keeps_the_order_asked_for(corpus, _output_dir):
    """parse_pages would sort and dedupe this into [1,3]. assemble must not:
    reordering pages is the operation, not an accident of input."""
    payload = engine.assemble([str(corpus["born_digital"])], "born_digital:3,1,1", "r.pdf")
    assert payload["result"]["pages_written"] == 3


def test_split_is_two_complementary_calls(corpus, _output_dir):
    first = engine.assemble([str(corpus["born_digital"])], "born_digital:1", "a.pdf")
    second = engine.assemble([str(corpus["born_digital"])], "born_digital:2-3", "b.pdf")
    assert first["result"]["pages_written"] + second["result"]["pages_written"] == 3


def test_an_unknown_source_key_lists_the_real_ones(corpus, _output_dir):
    payload = engine.assemble([str(corpus["born_digital"])], "nope:1", "x.pdf")
    _contract(payload, "assemble")
    assert payload["success"] is False
    assert "born_digital" in payload["hint"] and "s0" in payload["hint"]


def test_a_page_past_the_end_names_the_range(corpus, _output_dir):
    payload = engine.assemble([str(corpus["born_digital"])], "born_digital:99", "x.pdf")
    assert payload["success"] is False
    assert "1-3" in payload["hint"]


# --------------------------------------------------------------------------
# optimize
# --------------------------------------------------------------------------


def test_compress_reports_real_byte_counts(corpus, _output_dir):
    payload = engine.optimize(str(build.large()), "compress", out="c.pdf")
    _contract(payload, "optimize")
    result = payload["result"]
    # Real counts on both sides. A sibling repo divided by 1024 and made every
    # sub-kilobyte file "0 KB", including in a delete confirmation.
    assert result["bytes_before"] > 0 and result["bytes_after"] > 0
    assert result["bytes_after"] < result["bytes_before"]
    # ratio is rounded to 4 places by the tool, so the tolerance has to be the
    # rounding, not an arbitrary epsilon. 1e-6 asserted more precision than the
    # contract offers -- a test failing on its own tightness, not on a defect.
    assert abs(result["ratio"] - result["bytes_after"] / result["bytes_before"]) < 1e-4


def test_repair_recovers_a_file_the_fast_reader_refuses(corpus, _output_dir):
    """The damaged fixture is truncated: pdfium refuses it, QPDF recovers it.
    That gap IS what repair means here."""
    with pytest.raises(pdfium.PdfiumError):
        pdfium.PdfDocument(str(corpus["damaged"]))

    payload = engine.optimize(str(corpus["damaged"]), "repair", out="fixed.pdf")
    assert payload["success"] is True

    recovered = pdfium.PdfDocument(payload["result"]["out"])
    try:
        assert len(recovered) == 2
    finally:
        recovered.close()


def test_an_unknown_action_lists_the_real_ones(corpus, _output_dir):
    payload = engine.optimize(str(corpus["born_digital"]), "shrink", out="x.pdf")
    assert payload["success"] is False
    assert "compress" in payload["hint"]


def test_compress_says_so_when_a_file_did_not_shrink(corpus, _output_dir):
    """Reporting "compressed" for a file that grew is a claim its own numbers
    contradict."""
    payload = engine.optimize(str(corpus["born_digital"]), "compress", out="c.pdf")
    result = payload["result"]
    if result["bytes_after"] >= result["bytes_before"]:
        assert "did not get smaller" in result["note"]


# --------------------------------------------------------------------------
# protect
# --------------------------------------------------------------------------


def test_encrypt_then_decrypt_round_trips(corpus, _output_dir):
    locked = engine.protect(str(corpus["born_digital"]), "encrypt", password="hunter2", out="enc.pdf")
    _contract(locked, "protect")
    assert locked["result"]["encrypted"] is True

    # Confirm independently, not from the response.
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(locked["result"]["out"])

    opened = engine.protect(locked["result"]["out"], "decrypt", password="hunter2", out="dec.pdf")
    assert opened["result"]["encrypted"] is False
    with pikepdf.open(opened["result"]["out"]) as handle:
        assert len(handle.pages) == 3


def test_the_wrong_password_is_refused_without_a_recovery_path(corpus, _output_dir):
    locked = engine.protect(str(corpus["born_digital"]), "encrypt", password="right", out="enc.pdf")
    payload = engine.protect(locked["result"]["out"], "decrypt", password="wrong", out="x.pdf")
    _contract(payload, "protect")
    assert payload["success"] is False
    assert "recovery" in payload["hint"]


def test_decrypt_without_a_password_says_which_argument(corpus, _output_dir):
    payload = engine.protect(str(corpus["born_digital"]), "decrypt", out="x.pdf")
    assert payload["success"] is False
    assert "password=" in payload["hint"]


# --------------------------------------------------------------------------
# redact -- the tool that must verify
# --------------------------------------------------------------------------


def test_redacted_text_is_gone_from_the_file_not_covered(corpus, _output_dir):
    """Drawing a black box leaves the glyphs underneath for any extractor.
    This is the assertion the whole design exists for."""
    payload = engine.redact(str(corpus["born_digital"]), "quick brown fox", out="red.pdf")
    _contract(payload, "redact")
    assert payload["success"] is True
    assert payload["result"]["verified"] is True
    assert payload["result"]["redacted"] > 0

    document = pdfium.PdfDocument(payload["result"]["out"])
    try:
        for index in range(len(document)):
            assert "quick brown fox" not in document[index].get_textpage().get_text_bounded()
    finally:
        document.close()


def test_redact_never_reports_success_it_did_not_verify(corpus, _output_dir):
    payload = engine.redact(str(corpus["born_digital"]), "quick brown fox", out="red.pdf")
    # The impossible combination. If this ever holds, the file is unsafe and
    # the response says it is fine.
    assert not (payload["success"] and payload["result"]["verified"] is False)


def test_redacting_text_that_is_not_there_removes_nothing(corpus, _output_dir):
    payload = engine.redact(str(corpus["born_digital"]), "ZZZ-NOT-PRESENT", out="red.pdf")
    assert payload["success"] is True
    assert payload["result"]["redacted"] == 0
    assert payload["result"]["verified"] is True


def test_redact_needs_something_to_remove(corpus, _output_dir):
    payload = engine.redact(str(corpus["born_digital"]), "", out="x.pdf")
    assert payload["success"] is False
    assert "pattern=" in payload["hint"]


# --------------------------------------------------------------------------
# convert
# --------------------------------------------------------------------------


def test_pdf_to_text_and_html_and_images(corpus, _output_dir):
    for target in ("txt", "md", "html"):
        payload = engine.convert(str(corpus["born_digital"]), target, out=f"o.{target}")
        _contract(payload, "convert")
        assert payload["success"] is True, payload.get("error")
        written = _output_dir / f"o.{target}"
        assert written.exists() and written.stat().st_size > 0

    payload = engine.convert(str(corpus["born_digital"]), "images", out="pages")
    assert payload["result"]["files"] == 3
    assert (_output_dir / "pages" / "page_0001.png").exists()


def test_generated_html_stands_alone(corpus, _output_dir):
    """A file that needs a sibling or a network to render arrives broken."""
    engine.convert(str(corpus["born_digital"]), "html", out="o.html")
    body = (_output_dir / "o.html").read_text(encoding="utf-8")
    assert "<link" not in body and "<script" not in body and "http" not in body


def test_pdf_to_docx_is_refused_with_what_you_can_have_instead(corpus, _output_dir):
    """Reconstruction, not conversion. Shipping a bad one silently is worse
    than saying no -- and the hint names the tools that do work."""
    payload = engine.convert(str(corpus["born_digital"]), "docx", out="x.docx")
    _contract(payload, "convert")
    assert payload["success"] is False
    assert "extract_tables()" in payload["hint"]


def test_an_unknown_target_lists_the_real_ones(corpus, _output_dir):
    payload = engine.convert(str(corpus["born_digital"]), "epub", out="x")
    assert payload["success"] is False
    assert "images" in payload["hint"] and "md" in payload["hint"]


@pytest.mark.skipif(binaries.available("libreoffice"), reason="LibreOffice is installed here")
def test_a_missing_binary_names_the_package(corpus, _output_dir, tmp_path):
    """A caller can act on "install libreoffice-core"; nobody can act on
    errno 2 raised from inside a subprocess."""
    source = tmp_path / "note.txt"
    source.write_text("hello", encoding="utf-8")
    payload = engine.convert(str(source), "pdf", out="x.pdf")
    assert payload["success"] is False
    assert payload["available"] is False
    assert "apt-get install" in payload["hint"]


# --------------------------------------------------------------------------
# ocr
# --------------------------------------------------------------------------


def test_ocr_skips_pages_that_already_have_text(corpus, _output_dir):
    """OCR of a page with a good text layer replaces good text with worse."""
    payload = engine.ocr(str(corpus["born_digital"]), out="o.pdf")
    _contract(payload, "ocr")
    if payload["success"]:
        assert payload["result"]["pages_ocred"] == 0


def test_ocr_refuses_a_range_that_would_time_out(corpus, _output_dir):
    payload = engine.ocr(str(build.large()), pages="1-400", out="o.pdf")
    _contract(payload, "ocr")
    assert payload["success"] is False
    if payload.get("refused") == "budget":
        assert "pages=" in payload["hint"]
        assert payload["limit"] and payload["seen"]


@pytest.mark.skipif(not binaries.available("tesseract"), reason="Tesseract is not installed")
@realdocs.requires_real
def test_ocr_gives_a_scanned_document_a_real_text_layer(_output_dir, monkeypatch):
    """The end-to-end case: a real invoice that is a photograph, unreadable
    before and searchable after.

    The per-page timeout is pinned rather than inherited. This page OCRs in
    about 4 seconds on an idle machine and timed out at 120 on the same machine
    under a load average of 10 -- so the default, which is a sensible product
    decision, makes a poor test constant. Pinning it keeps the test about OCR
    quality instead of about how busy the host happens to be.
    """
    monkeypatch.setenv("DOCS_OCR_PAGE_TIMEOUT", "600")
    source = str(realdocs.path("scanned_invoice"))
    before = engine.ocr(source, out="ocred.pdf")
    assert before["success"] is True, before.get("error")
    assert before["result"]["pages_ocred"] == 1
    assert before["basis"] == "ocr"

    document = pdfium.PdfDocument(before["result"]["out"])
    try:
        recovered = document[0].get_textpage().get_text_bounded()
    finally:
        document.close()
    assert len(recovered.split()) > 50, "OCR produced no usable text layer"
