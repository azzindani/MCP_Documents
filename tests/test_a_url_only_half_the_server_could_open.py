"""`convert(source=<url>)` worked and `probe(source=<url>)` did not.

Found by Phase 5's remote smoke test, handing a URL to a docs-read tool on a
container with MCP_FETCH_URLS=1. The answer was:

    "No file at 'https://.../README.md'."
    "Check the path, or pass a URL if MCP_FETCH_URLS=1 is set."

A hint that names a capability the tier does not have, on a server where that
capability is switched ON -- worse than no hint, because a caller who followed
it got the same error again.

The two tiers resolved paths differently. docs-edit calls
`core.paths.resolve_source`, which fetches a URL into the inbox and returns a
local path; docs-read called `core.readers.open_source`, which routed on
`Path(source).suffix` of the RAW string and handed it straight to a reader.
Every reader then produced that same "or pass a URL" message from its own
not-found branch, so seven tools advertised a feature none of them had.

CLAUDE.md §11 states the property outright -- "MCP_FETCH_URLS=1 is what makes
*everything is fetchable* work: every path argument accepts an http(s) URL with
no per-tool change". It was true of six tools out of thirteen.

Fixed at `open_source`, one choke point, for the same reason
sanitize_responses and measure_responses wrap the whole server: the fourteenth
tool cannot forget it. That also puts shared/exchange.py's SSRF guard in front
of the read tier, which it was never behind -- an authenticated caller must not
be able to turn this server into a probe of the network it is deployed on.

Offline, like the rest of the suite: the fetch itself is stubbed, and the SSRF
case uses a literal address that needs no DNS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import cache
from core.readers import ReaderError, resolve
from servers.docs_read import engine as read
from tests.fixtures import build_formats

URL = "https://example.invalid/report.html"
METADATA_URL = "http://169.254.169.254/latest/meta-data/report.html"


@pytest.fixture(scope="session")
def formats():
    return build_formats.build_all()


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def fetching_on(monkeypatch):
    monkeypatch.setenv("MCP_FETCH_URLS", "1")


@pytest.fixture
def fetching_off(monkeypatch):
    monkeypatch.delenv("MCP_FETCH_URLS", raising=False)


class TestAUrlIsRecognisedAsAUrl:
    def test_with_fetching_off_the_refusal_names_the_switch(self, fetching_off):
        """Not "No file at" -- that told the caller to check a path they
        never gave, and offered a flag that would not have helped."""
        payload = read.probe(URL)
        assert payload["success"] is False
        assert "URL" in payload["error"] or "url" in payload["error"]
        assert "MCP_FETCH_URLS" in payload["hint"]
        assert "No file at" not in payload["error"]

    def test_the_error_is_a_ReaderError_not_a_PathError(self, fetching_off):
        """Every read tool catches ReaderError and none catches PathError.

        An untranslated one would raise straight through the tool layer
        instead of being answered, which is a 500 rather than a refusal.
        """
        with pytest.raises(ReaderError):
            resolve(URL)


class TestFetchingOnMakesEveryReadToolWork:
    @pytest.fixture
    def fetched(self, monkeypatch, fetching_on, formats) -> Path:
        """Stub the download. What is under test is the routing, not urllib."""
        local = formats["article_html"]
        monkeypatch.setattr("core.paths.fetch_url", lambda raw: local)
        return local

    def test_probe_reads_the_fetched_document(self, fetched):
        payload = read.probe(URL)
        assert payload["success"], payload
        assert payload["result"]["format"] == "html"

    def test_outline_reads_the_fetched_document(self, fetched):
        payload = read.outline(URL)
        assert payload["success"], payload
        assert payload["result"]["count"] >= 1

    def test_extract_reads_the_fetched_document(self, fetched):
        payload = read.extract(URL)
        assert payload["success"], payload
        assert "Quarterly Review" in payload["result"]["text"]

    def test_find_reads_the_fetched_document(self, fetched):
        payload = read.find(URL, "Revenue")
        assert payload["success"], payload
        assert payload["result"]["hits"] >= 1

    def test_read_page_reads_the_fetched_document(self, fetched):
        payload = read.read_page(URL, 1)
        assert payload["success"], payload

    def test_to_markdown_reads_the_fetched_document(self, fetched):
        payload = read.to_markdown(URL)
        assert payload["success"], payload

    def test_extract_tables_reads_the_fetched_document(self, fetched):
        payload = read.extract_tables(URL)
        assert payload["success"], payload

    def test_a_url_with_no_readable_extension_still_says_so(self, monkeypatch, fetching_on, formats):
        """Routing is on the FETCHED file, so the message names what arrived."""
        monkeypatch.setattr("core.paths.fetch_url", lambda raw: formats["article_html"].with_suffix(".zzz"))
        payload = read.probe("https://example.invalid/thing.html")
        assert payload["success"] is False
        assert "reader" in payload["error"].lower()


class TestTheSsrfGuardIsNowInFrontOfTheReadTier:
    def test_a_link_local_metadata_address_is_refused(self, fetching_on):
        """The cloud-metadata address, which is the one that matters.

        A literal IP, so this needs no DNS and no network -- the guard refuses
        before any connection is attempted.
        """
        payload = read.probe(METADATA_URL)
        assert payload["success"] is False
        assert "non-public" in payload["error"] or "non-public" in payload["hint"]

    def test_it_fires_for_every_read_tool_not_just_probe(self, fetching_on):
        for call in (
            lambda: read.extract(METADATA_URL),
            lambda: read.find(METADATA_URL, "x"),
            lambda: read.to_markdown(METADATA_URL),
        ):
            payload = call()
            assert payload["success"] is False
            assert "non-public" in payload["error"] or "non-public" in payload["hint"]


class TestADownloadFailureIsARefusalAndNotAnException:
    """fetch_url raises ValueError; nothing in either tier catches ValueError.

    So the guard that WAS reachable -- docs-edit has always resolved paths this
    way -- did not produce a refusal either. It raised through the tool layer,
    out of a server whose contract is that every failure is a dict carrying an
    `error` and a `hint`. Both tiers are checked here, because the fix is in
    the one function they share and a test of only one would not notice if a
    tier stopped using it.
    """

    def test_the_edit_tier_answers_with_a_refusal(self, fetching_on):
        from servers.docs_edit import engine as edit

        payload = edit.convert(METADATA_URL, "md")
        assert payload["success"] is False
        assert payload["error"] and payload["hint"]

    def test_the_hint_is_about_the_address_not_about_retrying(self, fetching_on):
        payload = read.probe(METADATA_URL)
        assert "publicly reachable" in payload["hint"]
        # Not the generic "check the URL opens in a browser" -- that is the
        # hint for a 404, and it is wrong advice for a blocked address.
        assert "opens in a browser" not in payload["hint"]

    def test_an_oversized_download_gets_its_own_hint(self, monkeypatch, fetching_on):
        def too_big(raw):
            raise ValueError(f"Download is larger than the 100 MB limit: {raw}")

        monkeypatch.setattr("core.paths.fetch_url", too_big)
        payload = read.probe(URL)
        assert payload["success"] is False
        assert "MCP_MAX_FETCH_MB" in payload["hint"]

    def test_an_ordinary_download_failure_gets_the_general_hint(self, monkeypatch, fetching_on):
        def broken(raw):
            raise ValueError(f"Could not download {raw}: HTTP Error 404: Not Found")

        monkeypatch.setattr("core.paths.fetch_url", broken)
        payload = read.probe(URL)
        assert payload["success"] is False
        assert "opens in a browser" in payload["hint"]


class TestLocalPathsAreUnchanged:
    def test_a_real_file_still_opens(self, formats):
        assert read.probe(str(formats["article_html"]))["success"]

    def test_a_missing_file_still_says_no_file_at(self, tmp_path):
        payload = read.probe(str(tmp_path / "absent.pdf"))
        assert payload["success"] is False
        assert "No file at" in payload["error"]

    def test_a_directory_is_refused_as_a_directory(self, tmp_path):
        payload = read.probe(str(tmp_path))
        assert payload["success"] is False
        assert "directory" in payload["error"]
