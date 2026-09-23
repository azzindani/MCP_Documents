"""A remote server reads and writes only inside the folders it serves.

`resolve_source` and `resolve_out` -- the two choke points every tool in both
tiers goes through -- resolved whatever path they were handed, so any
authenticated caller of the deployed server could read any file the container
could (`probe("/etc/hostname")`; `/proc/self/environ` holds the API keys) and
write wherever the process could.

With MCP_CONFINE_PATHS on (the default for every HTTP deployment) a path must
lie inside MCP_OUTPUT_DIR or MCP_ALLOWED_ROOTS, judged after symlinks resolve
and before the file is looked for, so a refusal says nothing about what exists
outside. A relative path is read from the data folder. A local stdio install
is unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.paths import PathError, resolve_out, resolve_source
from servers.docs_read import engine as read

OUTSIDE = "/etc/hostname" if Path("/etc/hostname").exists() else str(Path(__file__).resolve())


@pytest.fixture
def served(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("MCP_CONFINE_PATHS", "1")
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(data))
    monkeypatch.delenv("MCP_DATA_ROOT", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ROOTS", raising=False)
    (data / "note.txt").write_text("hello\n", encoding="utf-8")
    return data


class TestConfined:
    def test_a_source_outside_is_refused(self, served):
        with pytest.raises(PathError, match="outside the folders"):
            resolve_source(OUTSIDE)

    def test_a_missing_file_outside_is_refused_not_reported_missing(self, served):
        with pytest.raises(PathError, match="outside the folders"):
            resolve_source("/nonexistent-dir/secret.pdf")

    def test_a_relative_source_is_read_from_the_data_folder(self, served):
        assert resolve_source("note.txt") == (served / "note.txt").resolve()

    def test_climbing_out_is_refused(self, served):
        with pytest.raises(PathError, match="outside the folders"):
            resolve_source("../escape.pdf")

    def test_an_output_outside_is_refused_and_leaves_nothing(self, served, tmp_path):
        target = tmp_path / "elsewhere" / "out.pdf"
        with pytest.raises(PathError, match="outside the folders"):
            resolve_out(str(target))
        assert not target.parent.exists(), "a refused path must not leave a directory behind"

    def test_a_symlink_is_judged_by_where_it_leads(self, served, tmp_path):
        outside = tmp_path / "outside.txt"
        outside.write_text("x", encoding="utf-8")
        link = served / "innocent.txt"
        try:
            link.symlink_to(outside)
        except OSError, NotImplementedError:
            pytest.skip("cannot create a symlink here")
        with pytest.raises(PathError, match="outside the folders"):
            resolve_source(str(link))

    def test_an_extra_root_can_be_served(self, served, tmp_path, monkeypatch):
        extra = tmp_path / "extra"
        extra.mkdir()
        (extra / "a.txt").write_text("a", encoding="utf-8")
        monkeypatch.setenv("MCP_ALLOWED_ROOTS", str(extra))
        assert resolve_source(str(extra / "a.txt")) == (extra / "a.txt").resolve()

    def test_the_tool_refuses_with_a_hint(self, served):
        r = read.probe(OUTSIDE)
        assert r["success"] is False
        assert "outside the folders" in r["error"]
        assert r.get("hint")


class TestLocal:
    def test_a_local_install_is_not_confined(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MCP_CONFINE_PATHS", raising=False)
        f = tmp_path / "anywhere.txt"
        f.write_text("x", encoding="utf-8")
        assert resolve_source(str(f)) == f.resolve()


class TestAPathOnTheCallersSideIsNamedAsOne:
    """A claude.ai upload path is refused for what it is, with the way in.

    `/mnt/user-data/uploads/Ad_Data.csv` is the only path a chat's model holds
    for an attached file. "Outside the folders this server can use" named the
    rule and sent it guessing folders on a server that cannot see the file.
    """

    def test_the_refusal_says_the_file_is_on_the_callers_side(self, served, monkeypatch):
        monkeypatch.setenv("MCP_FETCH_URLS", "1")
        with pytest.raises(PathError) as caught:
            resolve_source("/mnt/user-data/uploads/Ad_Data.csv")
        message = str(caught.value)
        assert "caller's side" in message
        assert "cannot see it" in message
        assert "link" in message

    def test_any_other_outside_path_keeps_the_plain_refusal(self, served):
        with pytest.raises(PathError, match="outside the folders"):
            resolve_source("/etc/hostname")
