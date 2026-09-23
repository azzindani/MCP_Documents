"""A missing file is answered with the nearest files that exist.

"No file at 'q3_report.pdf'", hint "Check the path". A remote caller shares no
filesystem with the server and cannot look, so it guessed again; the file it
meant, one case-fold or one extension away, was visible only to the server.

Asserted through a real tool, and asserted never to name anything outside the
served folders.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.paths import PathError, missing_file_hint, resolve_source
from servers.docs_read import engine as read


@pytest.fixture
def served(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "archive").mkdir(parents=True)
    monkeypatch.setenv("MCP_CONFINE_PATHS", "1")
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(data))
    monkeypatch.delenv("MCP_DATA_ROOT", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ROOTS", raising=False)
    (data / "Q3_Report.pdf").write_bytes(b"%PDF-1.4\n")
    (data / "archive" / "minutes_2023.docx").write_bytes(b"")
    (data / ".hidden.pdf").write_bytes(b"")
    (data / "Q3_Report.pdf.mcp_receipt.json").write_text("{}")
    return data


class TestThroughTheTool:
    def test_the_exact_name_found_elsewhere_is_said_to_be_there(self, served):
        # Found by the sweep: "Nothing is named X there. Closest: X" -- the
        # exact name, found where the tool did not look, reported as absent.
        with pytest.raises(PathError) as exc:
            resolve_source("archive/Q3_Report.pdf")
        assert "Nothing is named" not in exc.value.hint
        assert "'Q3_Report.pdf' is in the data folder" in exc.value.hint

    def test_a_case_fold_away_is_named(self, served):
        if (served / "q3_report.pdf").exists():
            pytest.skip("case-insensitive filesystem: the file is found, nothing to suggest")
        r = read.probe("q3_report.pdf")
        assert r["success"] is False
        assert "Q3_Report.pdf" in r["hint"]

    def test_a_misremembered_extension_finds_the_real_one(self, served):
        with pytest.raises(PathError) as exc:
            resolve_source("Q3_Report.docx")
        assert "Closest: Q3_Report.pdf" in exc.value.hint

    def test_the_fleets_own_sidecars_are_never_suggested(self, served):
        with pytest.raises(PathError) as exc:
            resolve_source("Q3_Report.docx")
        assert ".mcp_" not in exc.value.hint

    def test_a_file_in_a_subfolder_is_named_by_the_path_to_pass(self, served):
        with pytest.raises(PathError) as exc:
            resolve_source("minutes_2024.docx")
        assert str(Path("archive") / "minutes_2023.docx") in exc.value.hint

    def test_nothing_close_lists_what_the_folder_holds(self, served):
        with pytest.raises(PathError) as exc:
            resolve_source("zzqx.epub")
        assert "Q3_Report.pdf" in exc.value.hint
        assert ".hidden.pdf" not in exc.value.hint
        assert ".mcp_" not in exc.value.hint, "a receipt is bookkeeping, not a file to pass"


class TestNeverOutside:
    def test_a_folder_outside_the_served_ones_is_never_listed(self, served, tmp_path):
        outside = tmp_path / "private"
        outside.mkdir()
        (outside / "salaries.pdf").write_bytes(b"")
        assert "salaries.pdf" not in missing_file_hint(outside / "salary.pdf")


class TestLocal:
    def test_the_named_folder_is_searched(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MCP_CONFINE_PATHS", raising=False)
        (tmp_path / "contract_v2.pdf").write_bytes(b"")
        assert str((tmp_path / "contract_v2.pdf").resolve()) in missing_file_hint(tmp_path / "contract_v3.pdf")
