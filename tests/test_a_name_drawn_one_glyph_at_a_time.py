"""Redacting a name the page draws one glyph at a time -- and leaving nothing when it cannot.

A SuperStore invoice sets every character in its own Tj: 367 operators, `V`,
`i`, `v`, `e`, `k`. redact tested each operator on its own, so no operator ever
held "Vivek Grady"; nothing was removed, verification refused -- and the refusal
left inv9_redacted.pdf on disk with the name in it, under "Do not distribute".

The runs are now joined in stream order and matched once, a literal's words may
be drawn with or without a space glyph between them, and the file is written
beside its destination and moved into place only after the check passes.
"""

from __future__ import annotations

import pikepdf
import pypdfium2 as pdfium
import pytest

from servers.docs_edit import _edit_secure
from servers.docs_edit import engine as edit
from tests.fixtures import build


def _text(path) -> str:
    doc = pdfium.PdfDocument(str(path))
    try:
        return doc[0].get_textpage().get_text_bounded()
    finally:
        doc.close()


def _pdf(tmp_path, name: str, content: bytes):
    pdf = pikepdf.Pdf.new()
    pdf.pages.append(build._page(pdf, content))
    path = tmp_path / name
    pdf.save(path)
    return path


def _glyph_by_glyph(tmp_path):
    items = [(72.0, 100.0, "Bill To:", 11.0), (72.0, 160.0, "Total: $1,906.05", 11.0)]
    x = 72.0
    for ch in "Vivek Grady":
        items.append((x, 120.0, ch, 11.0))
        x += 6.5
    return _pdf(tmp_path, "glyphs.pdf", build._text_ops(items))


@pytest.fixture(autouse=True)
def _out(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path))


class TestMatchedAcrossRuns:
    def test_one_glyph_per_operator(self, tmp_path):
        r = edit.redact(str(_glyph_by_glyph(tmp_path)), "Vivek Grady", out=str(tmp_path / "out.pdf"))
        assert r["success"] is True, r
        assert (r["result"]["redacted"], r["result"]["removed_beyond_match"], r["result"]["verified"]) == (11, 0, True)
        text = _text(tmp_path / "out.pdf")
        assert "Vivek" not in text and "Grady" not in text
        assert "Bill To:" in text and "Total: $1,906.05" in text

    def test_words_placed_without_a_space_glyph(self, tmp_path):
        src = _pdf(
            tmp_path,
            "words.pdf",
            build._text_ops(
                [(72.0, 120.0, "Vivek", 11.0), (110.0, 120.0, "Grady", 11.0), (72.0, 150.0, "Keep me", 11.0)]
            ),
        )
        r = edit.redact(str(src), "Vivek Grady", out=str(tmp_path / "out.pdf"))
        assert r["success"] is True and r["result"]["redacted"] == 2
        assert "Keep me" in _text(tmp_path / "out.pdf")

    def test_a_run_holding_more_than_the_match_goes_whole_and_is_counted(self, tmp_path):
        src = _pdf(
            tmp_path,
            "run.pdf",
            build._text_ops([(72.0, 120.0, "Bill To: Vivek Grady", 11.0), (72.0, 150.0, "Keep me", 11.0)]),
        )
        r = edit.redact(str(src), "Vivek Grady", out=str(tmp_path / "out.pdf"))
        assert r["success"] is True
        assert (r["result"]["redacted"], r["result"]["removed_beyond_match"]) == (1, 1)
        assert any("outside the match" in p["message"] for p in r["progress"])


class TestNothingIsLeftWhenTheCheckFails:
    def test_no_file_at_the_destination_and_no_staging_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_edit_secure, "_still_extractable", lambda path, matcher, pages: [1])
        r = edit.redact(str(_glyph_by_glyph(tmp_path)), "Vivek Grady", out=str(tmp_path / "leak.pdf"))
        assert r["success"] is False
        assert r["written"] is False and r["out"] is None
        assert "Nothing was written" in r["error"]
        assert not (tmp_path / "leak.pdf").exists()
        assert not list(tmp_path.glob(".*unverified*"))

    def test_a_verified_file_replaces_one_already_there(self, tmp_path):
        target = tmp_path / "out.pdf"
        target.write_bytes(b"old")
        r = edit.redact(str(_glyph_by_glyph(tmp_path)), "Vivek Grady", out=str(target))
        assert r["success"] is True and target.read_bytes().startswith(b"%PDF")


def test_a_deleted_quote_operator_keeps_its_line_break(tmp_path):
    """`'` moves to the next line and then shows text; deleting it whole moved every line under it up."""
    content = b"BT /F1 11 Tf 14 TL 72 700 Td (first line) Tj (Vivek Grady) ' (third line) ' ET"
    src = _pdf(tmp_path, "quote.pdf", content)
    r = edit.redact(str(src), "Vivek Grady", out=str(tmp_path / "out.pdf"))
    assert r["success"] is True

    def third_line_top(path):
        doc = pdfium.PdfDocument(str(path))
        try:
            page = doc[0].get_textpage()
            index = page.get_text_bounded().index("third")
            return page.get_charbox(index)[3]
        finally:
            doc.close()

    assert third_line_top(tmp_path / "out.pdf") == pytest.approx(third_line_top(src), abs=0.5)
