"""Reading `a.zip::member` adds nothing to the folder outputs go to.

A member was extracted into MCP_OUTPUT_DIR/inbox to be read. On a deployment
that folder is the user's own, so a read-only probe of `filing.zip::x.xbrl`
left a copy of the member there, and convert(to="file", out=<elsewhere>) left
one beside the file it was asked for -- a 1.8 MB copy found in a user's folder
on 2026-09-27. Members now unpack into a cache private to the server.
"""

from __future__ import annotations

import sys
import zipfile

import pytest

from core.paths import member_cache
from servers.docs_edit import engine as edit
from servers.docs_read import engine as read


@pytest.fixture
def folders(tmp_path, monkeypatch):
    outputs, cache, elsewhere = tmp_path / "outputs", tmp_path / "cache", tmp_path / "elsewhere"
    for folder in (outputs, elsewhere):
        folder.mkdir()
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(outputs))
    monkeypatch.setenv("DOCS_MEMBER_CACHE", str(cache))
    bundle = elsewhere / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("notes.txt", "The quarterly figure is 1,640,830.\n")
    return {"outputs": outputs, "cache": cache, "elsewhere": elsewhere, "bundle": bundle}


def _everything_in(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


def test_read_tools_write_nothing_where_outputs_go(folders):
    member = f"{folders['bundle']}::notes.txt"
    assert read.probe(member)["success"] is True
    assert read.find(member, "1,640,830")["result"]["hits"] == 1
    assert read.extract(member)["success"] is True
    assert _everything_in(folders["outputs"]) == []
    assert any(p.name.endswith("notes.txt") for p in folders["cache"].iterdir())


def test_saving_a_member_writes_only_the_file_asked_for(folders):
    out = folders["elsewhere"] / "notes_copy.txt"
    r = edit.convert(f"{folders['bundle']}::notes.txt", "file", out=str(out))
    assert r["success"] is True and out.read_text().startswith("The quarterly figure")
    assert _everything_in(folders["outputs"]) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_cache_is_private(folders):
    assert (member_cache().stat().st_mode & 0o777) == 0o700
