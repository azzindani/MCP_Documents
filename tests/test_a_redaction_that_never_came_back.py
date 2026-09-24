"""redact(regex=True) ran a caller's pattern with nothing bounding it.

find() has had its guard since core/scan.py; redact matched every text run of
every page with a bare `re` pattern, and `re` has no timeout. A run of forty
a's and a `!`, under `(a+)+$`, holds the call for longer than anyone waits --
the same worker-that-never-comes-back find was fixed for.

It now matches in a worker the server can stop (shared/regex_guard.py, the
file DA, FS and Web_Browser ship too), under the same budget find uses,
DOCS_REGEX_SECONDS:

- a runaway pattern is refused inside the budget, the way find refuses it
  (refused: budget, the limit, regex=False in the hint), and nothing is written;
- a pattern that finishes redacts exactly what the literal redacts, verified.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest

from servers.docs_edit import engine
from tests.fixtures import build

RUNAWAY = r"(a+)+$"


@pytest.fixture(autouse=True)
def _output_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("DOCS_REGEX_SECONDS", "1")
    return tmp_path


@pytest.fixture(scope="module")
def stuck_pdf() -> Path:
    pdf = pikepdf.Pdf.new()
    body = [(72.0, 100.0, "a" * 40 + "!", 11.0), (72.0, 120.0, "ACCOUNT-1234 is on file.", 11.0)]
    pdf.pages.append(build._page(pdf, build._text_ops(body)))
    return build._save(pdf, "stuck_run.pdf")


@pytest.fixture(scope="module")
def born_digital() -> Path:
    return build.born_digital()


def _text(path: str) -> str:
    document = pdfium.PdfDocument(path)
    try:
        return "\n".join(document[n].get_textpage().get_text_bounded() for n in range(len(document)))
    finally:
        document.close()


class TestARunawayPatternIsStopped:
    def test_refused_inside_the_budget_and_nothing_written(self, stuck_pdf, _output_dir):
        began = time.perf_counter()
        payload = engine.redact(str(stuck_pdf), RUNAWAY, regex=True, out="red.pdf")
        assert time.perf_counter() - began < 20, "the budget did not stop it"
        assert payload["success"] is False, payload
        assert payload["refused"] == "budget" and payload["limit"] == "1s"
        assert RUNAWAY in payload["error"]
        assert "regex=False" in payload["hint"] and "quantifier" in payload["hint"]
        assert not (_output_dir / "red.pdf").exists(), "nothing written"


class TestAPatternThatFinishesRedactsAsBefore:
    def test_the_same_runs_as_the_literal(self, born_digital, _output_dir):
        literal = engine.redact(str(born_digital), "quick brown fox", out="literal.pdf")
        pattern = engine.redact(str(born_digital), r"quick\s+brown\s+f.x", regex=True, out="pattern.pdf")
        assert literal["success"] is True and pattern["success"] is True, pattern
        assert pattern["result"]["verified"] is True
        assert pattern["result"]["redacted"] == literal["result"]["redacted"] > 0
        assert _text(pattern["result"]["out"]) == _text(literal["result"]["out"])

    def test_a_pattern_on_a_page_with_a_stuck_run_elsewhere(self, stuck_pdf):
        payload = engine.redact(str(stuck_pdf), r"ACCOUNT-\d{4}", regex=True, out="acct.pdf")
        assert payload["success"] is True, payload
        assert payload["result"]["verified"] is True and payload["result"]["redacted"] == 1
        assert "ACCOUNT-1234" not in _text(payload["result"]["out"])


def test_the_guard_is_one_file_across_the_fleet():
    root = Path(__file__).resolve().parents[1]
    mine = hashlib.sha256((root / "shared" / "regex_guard.py").read_bytes()).hexdigest()
    siblings = [
        root.parent / repo / "shared" / "regex_guard.py"
        for repo in ("MCP_Data_Analyst", "MCP_File_System", "MCP_Web_Browser")
    ]
    present = [p for p in siblings if p.exists()]
    if not present:
        pytest.skip("no sibling repo checked out beside this one")
    for p in present:
        assert hashlib.sha256(p.read_bytes()).hexdigest() == mine, p
