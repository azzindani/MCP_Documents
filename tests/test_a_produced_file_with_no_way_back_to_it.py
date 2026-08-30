"""Every file this server produced came back as a path inside the container.

Found by Phase 5's remote smoke test, calling convert() with no `out` against a
container configured for the file exchange:

    "out": "/files/notes_out.md"        and no public_url

`/files` is a path in the container's filesystem. A remote caller cannot open
it, and cannot tell it from a path they could.

`shared/exchange.py` has carried `attach_public_url` since the file arrived
from a sibling repo, and CLAUDE.md §11 states the exchange is wired up here
"byte-identical with the four file-producing repos". Nothing in this repo ever
called it. The half that was implemented -- MCP_OUTPUT_DIR, so output lands in
a directory the operator shares -- made the gap invisible: files went to the
right place and the response gave no way to reach them.

Fixed at `core.formatter.ok`, so it is one place rather than six. Every
file-producing tool here already reports its path as `result["out"]`; the
fourteenth cannot forget.

On the SUCCESS path only. redact() reports `out` on failure too, and that is
the file whose own hint says "Do not distribute this file" -- handing back a
public URL for it would be the opposite of the warning.
"""

from __future__ import annotations

import pytest

from servers.docs_edit import engine as edit
from tests.fixtures import build, build_formats

BASE = "https://files.example.invalid/shared"


@pytest.fixture(scope="session")
def corpus():
    return build.build_all(include_large=False)


@pytest.fixture(scope="session")
def formats():
    return build_formats.build_all()


@pytest.fixture
def exchange(monkeypatch, tmp_path):
    """A configured deployment: an output dir that a base URL serves."""
    shared = tmp_path / "files"
    shared.mkdir()
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(shared))
    monkeypatch.setenv("MCP_PUBLIC_BASE_URL", BASE)
    return shared


class TestAProducedFileCarriesItsUrl:
    def test_convert_with_no_out_lands_in_the_shared_dir_and_says_where(self, exchange, formats):
        payload = edit.convert(str(formats["plain_txt"]), "md")
        assert payload["success"], payload
        assert payload["result"]["out"].startswith(str(exchange))
        assert payload["result"]["public_url"] == f"{BASE}/{formats['plain_txt'].stem}_out.md"

    def test_optimize_carries_it_too(self, exchange, corpus):
        payload = edit.optimize(str(corpus["born_digital"]), "compress")
        assert payload["success"], payload
        assert payload["result"]["public_url"].startswith(BASE)

    def test_assemble_carries_it_too(self, exchange, corpus):
        source = str(corpus["born_digital"])
        payload = edit.assemble([source], "born_digital:1-2", "merged.pdf")
        assert payload["success"], payload
        assert payload["result"]["public_url"] == f"{BASE}/merged.pdf"

    def test_protect_carries_it_too(self, exchange, corpus):
        payload = edit.protect(str(corpus["born_digital"]), "encrypt", "pw", "locked.pdf")
        assert payload["success"], payload
        assert payload["result"]["public_url"] == f"{BASE}/locked.pdf"

    def test_redact_carries_it_when_it_succeeded(self, exchange, corpus):
        payload = edit.redact(str(corpus["born_digital"]), "quick brown fox", out="clean.pdf")
        assert payload["success"], payload
        assert payload["result"]["public_url"] == f"{BASE}/clean.pdf"

    def test_a_name_with_a_space_is_escaped(self, exchange, corpus):
        """A URL is not a path. An unescaped space produces a broken link."""
        payload = edit.optimize(str(corpus["born_digital"]), "compress", "quarterly report.pdf")
        assert payload["success"], payload
        assert payload["result"]["public_url"] == f"{BASE}/quarterly%20report.pdf"


class TestItIsSilentWhenThereIsNothingToSay:
    def test_no_base_url_means_no_public_url(self, monkeypatch, tmp_path, corpus):
        """A local stdio install has no file server and must not invent one."""
        monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path))
        monkeypatch.delenv("MCP_PUBLIC_BASE_URL", raising=False)
        payload = edit.optimize(str(corpus["born_digital"]), "compress")
        assert payload["success"], payload
        assert "public_url" not in payload["result"]

    def test_a_file_written_outside_the_shared_dir_gets_no_url(self, exchange, corpus, tmp_path):
        """The base URL serves MCP_OUTPUT_DIR, not the whole filesystem.

        A URL computed for a path the file server cannot see is worse than no
        URL: it is a link that 404s, which reads as "the file is gone".
        """
        elsewhere = tmp_path / "private" / "out.pdf"
        elsewhere.parent.mkdir()
        payload = edit.optimize(str(corpus["born_digital"]), "compress", str(elsewhere))
        assert payload["success"], payload
        assert "public_url" not in payload["result"]

    def test_a_read_tool_gets_no_url_because_it_produced_no_file(self, exchange, corpus):
        from servers.docs_read import engine as read

        payload = read.probe(str(corpus["born_digital"]))
        assert payload["success"], payload
        assert "public_url" not in payload["result"]


class TestAFailedRedactionIsNotPublished:
    def test_an_unverified_redaction_carries_no_public_url(self, exchange, monkeypatch, corpus):
        """Its own hint says "Do not distribute this file"."""
        # Force the failure the way it happens in life: nothing matched, so the
        # pattern is still extractable from the output.
        monkeypatch.setattr("servers.docs_edit._edit_secure._strip_from_page", lambda *a, **k: 0)
        payload = edit.redact(str(corpus["born_digital"]), "quick brown fox", out="unverified.pdf")
        assert payload["success"] is False
        assert payload["verified"] is False
        assert "public_url" not in payload
        assert "Do not distribute" in payload["hint"]
