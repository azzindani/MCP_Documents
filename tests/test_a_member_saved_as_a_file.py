"""One member of a .zip, saved as a file of its own: convert(to="file").

A member could be listed (probe("a.zip")) and read (probe("a.zip::x.xbrl")),
and never had: reading extracts it into the inbox, a directory the caller does
not see, and a member this server has no reader for -- a CSV for the data
server, an image -- could not be reached at all. There is no shell here, so
"unzip it first" is not advice a caller can take.

It is a target on convert and not a 14th tool (docs/DECISIONS.md §3). The
archive passes the guards probe applies before any member leaves it, so a
small member does not carry a bomb's archive past the total-bytes guard.
"""

from __future__ import annotations

import asyncio
import zipfile

import pytest

from core.paths import resolve_source
from servers.docs_edit import engine as edit

CSV = b"region,units\nAPAC,3\nEMEA,5\n"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(data))
    return data


@pytest.fixture
def bundle(tmp_path, out_dir):
    path = tmp_path / "filing.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("tables/sales.csv", CSV)
        zf.writestr("logo.png", PNG)
    return path


class TestTheMemberIsSaved:
    def test_byte_for_byte_where_outputs_go(self, bundle, out_dir):
        r = edit.convert(f"{bundle}::tables/sales.csv", "file")
        assert r["success"] is True, r
        assert r["result"]["out"] == str(out_dir / "sales_out.csv")
        assert (out_dir / "sales_out.csv").read_bytes() == CSV
        assert (r["result"]["to"], r["result"]["member"], r["result"]["bytes"]) == (
            "file",
            "tables/sales.csv",
            len(CSV),
        )

    def test_a_format_this_server_does_not_read(self, bundle, out_dir):
        r = edit.convert(f"{bundle}::logo.png", "file", out="logo.png")
        assert r["success"] is True, r
        assert (out_dir / "logo.png").read_bytes() == PNG

    def test_the_target_is_in_the_schema(self):
        from servers.docs_edit.server import mcp

        tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "convert")
        assert "file" in tool.inputSchema["properties"]["to"]["enum"]


class TestWhatIsRefused:
    @pytest.mark.parametrize("source", ["{bundle}", "{bundle}::", "{csv}"])
    def test_no_member_named(self, bundle, out_dir, source):
        (out_dir / "plain.csv").write_bytes(CSV)
        r = edit.convert(source.format(bundle=bundle, csv=out_dir / "plain.csv"), "file")
        assert r["success"] is False
        assert "archive.zip::member" in r["hint"] or "probe(" in r["hint"]

    def test_a_member_that_is_not_there_names_the_ones_that_are(self, bundle):
        r = edit.convert(f"{bundle}::sales.csv", "file")
        assert r["success"] is False
        assert "tables/sales.csv" in r["hint"]

    def test_a_member_that_walks_upward(self, bundle):
        r = edit.convert(f"{bundle}::../../etc/passwd", "file")
        assert r["success"] is False and "safe member" in r["error"]

    def test_the_archive_total_is_guarded_even_when_the_member_fits(self, tmp_path, out_dir, monkeypatch):
        rows = b"a,b\n1,2\n" * 75  # 600 bytes, packs ~20x: no ratio alarm
        path = tmp_path / "two.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("one.csv", rows)
            zf.writestr("two.csv", rows)
        monkeypatch.setenv("DOCS_MAX_SOURCE_BYTES", "1000")
        assert resolve_source(f"{path}::one.csv").read_bytes() == rows, "the member alone fits"
        r = edit.convert(f"{path}::one.csv", "file")
        assert r["success"] is False and "expands to" in r["error"]
        assert not (out_dir / "one_out.csv").exists()

    def test_a_bomb_is_refused(self, tmp_path, out_dir):
        path = tmp_path / "bomb.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.txt", "0" * 40_000_000)
            zf.writestr("small.csv", CSV)
        r = edit.convert(f"{path}::small.csv", "file")
        assert r["success"] is False and "expands" in r["error"]
        assert not (out_dir / "small_out.csv").exists()
