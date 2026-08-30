"""ZIP and XBRL, through the same 13 tools as every other format.

Added because a financial filing arrives as a bundle, not as a file. The corpus
proves it: every IDX filing in `/root/Evals/investment` ships a PDF, a
workbook, a taxonomy zip and an instance zip, and the one carrying the numbers
in machine-readable form is inside an archive. Before this, a caller holding
that bundle had no way in -- this server has no shell, so "unzip it first" is
not advice they can take.

Two different shapes, deliberately:

**XBRL is a document.** It gets a reader, and its facts come back `native`,
which no other format's numbers do. Everything else here reconstructs figures
from layout; an instance states them.

**A ZIP is a container.** It does NOT get pages mapped onto members -- that
would make `extract(pages='3-5')` return three filenames and call it text. It
opens as its manifest, and a member is read by naming it:

    probe("filing.zip")                  -> what is inside, and how to open it
    probe("filing.zip::instance.xbrl")   -> the member, read as an XBRL

The selector is resolved in `core.paths.resolve_source`, which BOTH tiers call,
so it works in docs-edit too. Putting it in the read tier alone is exactly how
`source=<url>` once worked in one tier and not the other.
"""

from __future__ import annotations

import zipfile

import pytest

from core.paths import PathError, resolve_source
from servers.docs_read import engine as read
from tests.fixtures import real


@pytest.fixture(scope="module")
def bundle():
    return str(real.path("xbrl_instance_zip"))


@pytest.fixture(scope="module")
def instance(bundle):
    return f"{bundle}::instance.xbrl"


class TestAnArchiveOpensAsItsManifest:
    def test_probe_lists_the_members(self, bundle):
        result = read.probe(bundle)
        assert result["success"] is True
        assert result["result"]["format"] == "zip"
        details = result["result"]["format_details"]
        assert details["member_count"] == 2
        assert {m["name"] for m in details["members"]} == {"instance.xbrl", "Taxonomy.xsd"}

    def test_it_says_which_members_it_can_read(self, bundle):
        details = read.probe(bundle)["result"]["format_details"]
        assert details["readable_members"] == ["instance.xbrl"]
        # A listing with no way to act on it is a dead end.
        assert details["open_with"].endswith("::instance.xbrl")

    def test_members_are_not_pages(self, bundle):
        """The design decision, asserted so it cannot drift.

        Two members must not become two pages. A container has no pages, and
        page numbers that secretly mean member indices would corrupt every
        page-range argument in the server.
        """
        assert read.probe(bundle)["result"]["pages"] == 1

    def test_the_manifest_is_a_table(self, bundle):
        result = read.extract_tables(bundle)
        assert result["success"] is True
        table = result["result"]["tables"][0]
        assert table["basis"] == "native"
        assert table["rows"][0] == ["member", "format", "bytes", "readable"]


class TestTheMemberIsReadAsItsOwnFormat:
    def test_the_instance_reads_as_xbrl(self, instance):
        result = read.probe(instance)
        assert result["success"] is True
        assert result["result"]["format"] == "xbrl"
        assert result["basis"] == "native"

    def test_the_facts_are_the_ones_the_issuer_filed(self, instance):
        """Against the XBRL parsed independently, not against another tool."""
        oracle = real.xbrl_facts()
        assert oracle["Assets|CurrentYearInstant"] == 1_640_830_566

        found = read.find(instance, "1640830566000000")
        assert found["result"]["hits"] >= 1

    def test_facts_come_back_as_rows_with_their_context(self, instance):
        result = read.extract_tables(instance, pages="1")
        assert result["success"] is True
        table = result["result"]["tables"][0]
        assert table["basis"] == "native"
        assert table["rows"][0] == ["fact", "context", "unit", "value", "decimals"]

    def test_a_fact_row_carries_meaning_not_just_a_number(self, instance):
        """A value with no context is not an answer.

        `634224104000000` means nothing until you know it is savings deposits,
        at a date, in rupiah. Name, context and value are required of every
        fact; a UNIT is required only of a numeric one, because in XBRL only
        numeric facts have a unitRef -- `EntityName` is a tagged fact with a
        context and no unit, and demanding one here would be a test asserting
        something the format does not say.
        """
        rows = read.extract_tables(instance, pages="1")["result"]["tables"][0]["rows"][1:]
        assert rows
        numeric_seen = 0
        for name, context, unit, value, _decimals in rows[:40]:
            assert name and value
            assert context, f"{name} has a bare value and no context"
            if value.lstrip("-").replace(".", "", 1).isdigit():
                assert unit, f"{name} is numeric and has no unit"
                numeric_seen += 1
        assert numeric_seen, "no numeric fact in the sample; the unit rule went untested"

    def test_a_company_name_is_not_counted_as_a_number(self, instance):
        """`numeric_facts` counts units, not rows.

        Counting every non-narrative fact as numeric reports a company's name
        and its filing date among its figures.
        """
        details = read.probe(instance)["result"]["format_details"]
        assert details["other_facts"] > 0, "fixture has no non-numeric facts; the count is untested"
        assert details["numeric_facts"] + details["other_facts"] + details["text_blocks"] == details["facts"]

    def test_values_are_not_rescaled(self, instance):
        """Filed in full rupiah; the PDF beside it prints millions.

        Neither is converted, because rescaling would produce a figure matching
        neither document. Asserted through `find` rather than `extract`: the
        figure's presence is the claim, and `extract` of a page range is
        budget-bound, so under MCP_CONSTRAINED_MODE=1 it correctly refuses and
        this test would be measuring the budget instead of the value.
        """
        full_rupiah = read.find(instance, "1640830566000000")
        assert full_rupiah["success"] is True
        assert full_rupiah["result"]["hits"] >= 1

        # And the millions form the PDF prints must NOT appear as a value here.
        assert read.find(instance, "1.640.830.566")["result"]["hits"] == 0

    def test_probe_extras_name_the_entity_and_the_counts(self, instance):
        details = read.probe(instance)["result"]["format_details"]
        assert details["numeric_facts"] > 500
        assert details["contexts"] > 0
        assert "IDR" in details["units"]


class TestTheGuards:
    def test_a_member_that_walks_upward_is_refused(self, bundle):
        with pytest.raises(PathError) as caught:
            resolve_source(f"{bundle}::../etc/passwd")
        assert "not a safe member name" in str(caught.value)
        assert caught.value.hint

    def test_an_absolute_member_is_refused(self, bundle):
        with pytest.raises(PathError) as caught:
            resolve_source(f"{bundle}::/etc/passwd")
        assert "not a safe member name" in str(caught.value)

    def test_a_missing_member_lists_the_real_ones(self, bundle):
        with pytest.raises(PathError) as caught:
            resolve_source(f"{bundle}::nope.xbrl")
        assert "instance.xbrl" in caught.value.hint

    def test_naming_no_member_is_refused_with_a_way_forward(self, bundle):
        with pytest.raises(PathError) as caught:
            resolve_source(f"{bundle}::")
        assert "probe(" in caught.value.hint

    def test_a_decompression_bomb_is_refused(self, tmp_path):
        """A ratio far beyond anything a document archive reaches."""
        bomb = tmp_path / "bomb.zip"
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.txt", "0" * 40_000_000)
        result = read.probe(str(bomb))
        assert result["success"] is False
        assert "bomb" in result["error"].lower() or "expands" in result["error"]

    def test_a_real_filing_archive_is_not_mistaken_for_a_bomb(self, bundle):
        """The measurement that set the constant.

        Real XBRL archives compress 6.9x to 31x -- XML packs extremely well --
        so the intuitive "over 10x is a bomb" guard rejects every genuine
        filing in the corpus. This is the test that would have caught it.
        """
        with zipfile.ZipFile(bundle) as zf:
            entries = zf.infolist()
        ratio = sum(e.file_size for e in entries) / sum(e.compress_size for e in entries)
        assert ratio > 10, "fixture no longer exercises the constant it was chosen for"
        assert read.probe(bundle)["success"] is True

    def test_a_zip_that_is_not_a_zip_fails_with_a_hint(self, tmp_path):
        broken = tmp_path / "broken.zip"
        broken.write_bytes(b"this is not a zip")
        result = read.probe(str(broken))
        assert result["success"] is False
        assert result["hint"]


class TestTheOtherZipFormatsAreNotAffected:
    @pytest.mark.parametrize("filename", ["report.docx", "book.xlsx", "deck.pptx", "book.epub"])
    def test_a_zip_based_document_still_reads_as_a_document(self, filename):
        """docx, xlsx, pptx and epub are zips and must NOT route to the archive
        reader -- `probe("report.docx")` reads a document, not a listing of
        `word/document.xml`.
        """
        from tests.fixtures import build_formats

        corpus = build_formats.build_all()
        source = next((str(p) for p in corpus.values() if str(p).endswith(filename)), None)
        if source is None:
            pytest.skip(f"{filename} not built")
        result = read.probe(source)
        assert result["success"] is True
        assert result["result"]["format"] != "zip"

    def test_reaching_inside_a_docx_is_not_offered(self, tmp_path):
        """Only `.zip` is a container whose members can be named."""
        from tests.fixtures import build_formats

        corpus = build_formats.build_all()
        docx = next((str(p) for p in corpus.values() if str(p).endswith("report.docx")), None)
        if docx is None:
            pytest.skip("report.docx not built")
        with pytest.raises(PathError):
            resolve_source(f"{docx}::word/document.xml")


class TestAnIpv6UrlIsNotAMemberSelector:
    def test_a_bare_double_colon_in_a_url_is_left_alone(self):
        """`http://[::1]/report.pdf` contains `::` and is not an archive.

        Splitting on it would turn a request the SSRF guard exists to refuse
        into a member lookup that never reaches the guard at all.
        """
        with pytest.raises(PathError) as caught:
            resolve_source("http://[::1]/report.pdf")
        assert "not a safe member name" not in str(caught.value)
