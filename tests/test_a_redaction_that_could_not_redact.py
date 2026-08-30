"""redact() could not remove text from any PDF a word processor had made.

Found by Phase 5's remote smoke test, which converts an HTML file to PDF with
LibreOffice inside the container and then redacts a figure out of it. The
answer was:

    redacted: 0, verified: false, residual_matches: 1

`find()` located the value on the same page of the same file, so two tools in
one server disagreed about whether the text was there.

Two independent faults, both in how a content stream's bytes were read as text.

**The operand bytes are not the text.** Text was matched with
`bytes(operand).decode("latin-1")`, which is true only of a PDF set in a
base-14 font with no subsetting -- i.e. this repo's own fixtures, and almost
nothing else. LibreOffice, Word and every modern producer embed a SUBSET font
and address it by glyph index, so `1450.50` arrives as
b'\\x1e\\x00\\x00\\x00!"...'. The font's /ToUnicode CMap is what turns those
codes back into characters, and it was never consulted.

**`bytes(3)` is not the digit 3.** A TJ operand is an array of strings
interleaved with kerning numbers. pikepdf hands the numbers back as Python
ints, and `bytes(int)` allocates that many NUL bytes -- so every kern spliced
NULs into the middle of the text (or raised ValueError for a negative kern,
dropping that run of text entirely). The comment above the line said the
numbers were dropped. Nothing dropped them, and this one bites base-14 fonts
too: kerned text has never been redactable.

The tool was never WRONG about what it had done -- verification caught both
and refused the file every time, which is the whole reason that step is not
optional. It was simply unable to do the thing it exists for, and said so in a
hint that blamed the document.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest

from servers.docs_edit import engine as edit
from servers.docs_read import engine as read
from tests.fixtures import build


@pytest.fixture(scope="session")
def subset_pdf() -> Path:
    return build.subset_font()


@pytest.fixture(scope="session")
def kerned_pdf() -> Path:
    return build.kerned()


def _extracted(path: Path) -> str:
    document = pdfium.PdfDocument(str(path))
    try:
        return "\n".join(document[n].get_textpage().get_text_bounded() for n in range(len(document)))
    finally:
        document.close()


class TestTheFixturesHaveThePropertyTheyExistFor:
    """A fixture that does not isolate the fault proves nothing about the fix.

    If the subset PDF's bytes happened to contain the pattern, the redaction
    below would pass with the old code too -- so this is checked first.
    """

    def test_the_subset_pdf_stores_no_readable_bytes(self, subset_pdf):
        raw = b""
        with pikepdf.open(subset_pdf) as pdf:
            for operands, operator in pikepdf.parse_content_stream(pdf.pages[0]):
                if str(operator) == "Tj":
                    raw = bytes(operands[-1])
        assert raw, "no Tj operator found; the fixture is not what this test assumes"
        assert b"1450.50" not in raw
        assert b"Account" not in raw

    def test_but_the_text_is_still_extractable(self, subset_pdf):
        """Which is exactly why redacting it matters."""
        assert "1450.50" in _extracted(subset_pdf)

    def test_the_kerned_pdf_splits_the_value_across_array_elements(self, kerned_pdf):
        with pikepdf.open(kerned_pdf) as pdf:
            arrays = [
                operands[0]
                for operands, operator in pikepdf.parse_content_stream(pdf.pages[0])
                if str(operator) == "TJ"
            ]
        assert arrays, "no TJ operator found"
        kerns = [item for item in arrays[0] if isinstance(item, int | float)]
        assert kerns, "the fixture has no kerning numbers, so it tests nothing"
        # The sign matters more than the presence. `bytes(-30)` RAISES, and the
        # old code caught that and substituted "" -- accidentally correct. Only
        # a positive kern reaches `bytes(30)` and splices in thirty NULs, so a
        # fixture kerned only the usual way passes against the broken code.
        assert any(k > 0 for k in kerns), "only negative kerns: this fixture cannot fail against the old code"


class TestASubsetFontIsRedactable:
    def test_the_value_is_removed_and_verified(self, subset_pdf, tmp_path):
        out = tmp_path / "redacted.pdf"
        payload = edit.redact(str(subset_pdf), "1450.50", out=str(out))
        assert payload["success"], payload
        assert payload["result"]["redacted"] >= 1
        assert payload["result"]["verified"] is True
        assert payload["result"]["residual_matches"] == 0

    def test_and_a_second_tool_agrees_it_is_gone(self, subset_pdf, tmp_path):
        """The tool checking its own output is the weaker half of the proof."""
        out = tmp_path / "redacted.pdf"
        edit.redact(str(subset_pdf), "1450.50", out=str(out))
        assert read.find(str(out), "1450.50")["result"]["hits"] == 0
        assert "1450.50" not in _extracted(out)

    def test_a_pattern_that_is_not_there_removes_nothing(self, subset_pdf, tmp_path):
        """Decoding must not make the matcher generous.

        An unmapped glyph code becomes U+FFFD rather than nothing, precisely so
        a pattern cannot match ACROSS a character the font does not map --
        which would redact text the caller never named.
        """
        out = tmp_path / "untouched.pdf"
        payload = edit.redact(str(subset_pdf), "9999.99", out=str(out))
        assert payload["success"], payload
        assert payload["result"]["redacted"] == 0
        assert "Account" in _extracted(out)


class TestKerningNumbersAreNotText:
    def test_a_kerned_value_is_redactable(self, kerned_pdf, tmp_path):
        """`(1450) -30 (.50)` is one value, and the -30 is not part of it."""
        out = tmp_path / "redacted.pdf"
        payload = edit.redact(str(kerned_pdf), "1450.50", out=str(out))
        assert payload["success"], payload
        assert payload["result"]["redacted"] >= 1
        assert payload["result"]["verified"] is True

    def test_the_neighbouring_figure_survives(self, kerned_pdf, tmp_path):
        """A redaction that removes the whole page also passes "it is gone"."""
        out = tmp_path / "redacted.pdf"
        edit.redact(str(kerned_pdf), "1450.50", out=str(out))
        text = _extracted(out)
        assert "1450.50" not in text
        assert "1120.20" in text
        assert "Reference" in text


class TestTheOldFixturesStillWork:
    """The base-14, unkerned case the tool could always handle."""

    def test_a_plain_helvetica_document_is_still_redactable(self, tmp_path):
        source = build.born_digital(pages=2, name="redact_control.pdf")
        out = tmp_path / "redacted.pdf"
        payload = edit.redact(str(source), "quick brown fox", out=str(out))
        assert payload["success"], payload
        assert payload["result"]["redacted"] >= 1
        assert "quick brown fox" not in _extracted(out)
