"""Every format that is not PDF, through the same 13 tools.

The point of `core/readers` is that adding a format costs a reader module and
one line in READERS -- no new tool, no new response shape. So these tests call
the SAME tools the PDF tests call and assert the same contract, which is the
only way to find out whether that claim is true.

Each test is named after the wrong answer it exists to prevent. Three of them
are regressions for defects found by running these fixtures for the first time,
and all three produced a confident, plausible, wrong result rather than an
error -- which is the failure mode this repo is built against:

  * headings silently missing and the page count changing between two identical
    reads, from tracking visited lxml nodes by id()
  * a slide's speaker notes vanishing from every response, because reading
    order dropped blocks that carry no coordinates
  * outline() falling off the end of its function and returning None on the
    inferred path, unreachable behind a mis-indented return
"""

from __future__ import annotations

import json

import pytest

from core import cache
from servers.docs_edit import engine as edit
from servers.docs_read import engine as read
from tests.fixtures import build_formats


@pytest.fixture(scope="session")
def formats():
    return build_formats.build_all()


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Every test opens its documents fresh.

    The reader cache is keyed on (path, mtime, size), so it is correct across
    tests -- but a test that depends on another having warmed it is a test that
    passes for the wrong reason, and one that leaves a document open changes
    what the next one measures.
    """
    cache.clear()
    yield
    cache.clear()


def _contract(payload: dict, op: str) -> None:
    """The same response contract every tool in this repo owes, format aside."""
    assert payload["op"] == op
    assert isinstance(payload["success"], bool)
    assert isinstance(payload["token_estimate"], int)
    assert isinstance(payload["progress"], list)
    if not payload["success"]:
        assert payload["error"] and payload["hint"] and payload["hint"] != payload["error"]

    def reject(constant: str) -> None:
        raise AssertionError(f"non-JSON literal {constant} in the response")

    json.loads(json.dumps(payload), parse_constant=reject)


ALL_FIXTURES = (
    "article_html",
    "bare_html",
    "long_html",
    "readme_md",
    "sales_csv",
    "plain_txt",
    "message_eml",
    "html_only_eml",
    "report_docx",
    "deck_pptx",
    "book_xlsx",
    "book_epub",
)


# --------------------------------------------------------------------------
# The claim: one set of tools, every format
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_format_probes_with_the_same_contract(formats, name):
    payload = read.probe(str(formats[name]))
    _contract(payload, "probe")
    assert payload["success"], payload.get("error")
    result = payload["result"]
    assert result["pages"] >= 1
    assert result["pagination"] in {"native", "synthetic"}
    assert result["extractable"] in {"full", "partial", "none"}


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_format_extracts_something(formats, name):
    """One page, so the assertion is about the READER, not about the budget.

    Extracting whole documents here made this test fail under
    MCP_CONSTRAINED_MODE=1 for the one fixture deliberately built to exceed a
    response -- a correct refusal, reported as a broken reader. A test that
    changes its verdict with an unrelated environment variable is measuring the
    wrong thing; the refusal has its own test below.
    """
    payload = read.extract(str(formats[name]), pages="1")
    _contract(payload, "extract")
    assert payload["success"], payload.get("error")
    assert payload["result"]["text"].strip(), "a readable fixture extracted to nothing"


def test_an_unregistered_extension_is_refused_by_name(tmp_path):
    odd = tmp_path / "thing.xyz"
    odd.write_text("content")
    payload = read.probe(str(odd))
    _contract(payload, "probe")
    assert not payload["success"]
    assert ".xyz" in payload["error"]
    # The hint has to name what IS available, or the caller has no next move.
    assert ".pdf" in payload["hint"] and ".html" in payload["hint"]


# --------------------------------------------------------------------------
# Synthetic pagination is disclosed, real pagination is not called synthetic
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["article_html", "readme_md", "plain_txt", "report_docx", "message_eml"])
def test_flow_formats_say_their_pages_are_synthetic(formats, name):
    result = read.probe(str(formats[name]))["result"]
    assert result["pagination"] == "synthetic"
    assert result["chars_per_page"] > 0
    # No geometry to report, and a zero page size would read as a measurement.
    assert result["page_size"] is None


@pytest.mark.parametrize("name", ["deck_pptx", "book_xlsx", "book_epub"])
def test_formats_with_real_pages_do_not_claim_synthetic(formats, name):
    result = read.probe(str(formats[name]))["result"]
    assert result["pagination"] == "native"
    assert "chars_per_page" not in result


def test_a_long_flow_document_paginates_and_pages_are_findable(formats):
    probed = read.probe(str(formats["long_html"]))["result"]
    assert probed["pages"] > 1, "60 long paragraphs should not fit one synthetic page"

    first = read.find(str(formats["long_html"]), "Paragraph 001")["result"]["matches"]
    assert first and first[0]["page"] == 1
    last = read.find(str(formats["long_html"]), "Paragraph 060")["result"]["matches"]
    assert last and last[0]["page"] == probed["pages"], "find and probe disagree about the page count"


def test_reading_the_same_file_twice_gives_the_same_answer(formats):
    """A reader whose output changes between identical reads is the worst bug here.

    It was real: `_blocks` tracked visited lxml nodes in a set of `id()`s, and
    lxml builds a throwaway proxy per access whose id CPython recycles as soon
    as it is collected. Headings went missing at random and the same file came
    back as seven pages on one read and eight on the next -- with nothing
    visibly wrong in either response.
    """
    seen = set()
    for _ in range(8):
        cache.clear()
        result = read.probe(str(formats["long_html"]))["result"]
        seen.add((result["pages"], result["token_estimate_full"]))
    assert len(seen) == 1, f"the same document read {len(seen)} different ways: {sorted(seen)}"


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def test_html_drops_scripts_and_styles_entirely(formats):
    """Not by stripping tags -- by removing the subtree.

    The fixture's <script> contains a plausible English sentence rather than
    obvious code, because a stripper that merely removes angle brackets leaves
    `var total = 41` looking like debris a reader might excuse, and leaves a
    real sentence looking exactly like content.
    """
    text = read.extract(str(formats["article_html"]))["result"]["text"]
    assert "board approved the dividend" not in text
    assert "font-family" not in text
    assert "northern territories" in text


def test_html_headings_are_native_and_all_of_them_are_found(formats):
    payload = read.outline(str(formats["article_html"]))
    _contract(payload, "outline")
    entries = payload["result"]["entries"]
    assert [(e["level"], e["title"]) for e in entries] == [
        (1, "Quarterly Review"),
        (2, "Costs"),
        (3, "Outlook"),
    ]
    # HTML declares its headings. Reporting them at the PDF's inferred
    # confidence would throw away the difference between stated and guessed.
    assert payload["basis"] == "native"
    assert all(e["basis"] == "native" for e in entries)


def test_html_tables_are_native_not_inferred(formats):
    payload = read.extract_tables(str(formats["article_html"]))
    _contract(payload, "extract_tables")
    result = payload["result"]
    assert result["count"] == 1
    table = result["tables"][0]
    assert table["basis"] == "native" and table["confidence"] == 1.0
    assert table["rows"][0] == ["Region", "Revenue", "Change"]
    assert table["row_count"] == 3 and table["column_count"] == 3


def test_html_with_no_block_elements_still_yields_its_text(formats):
    """Unwrapped body text is ordinary in real HTML, not an edge case."""
    text = read.extract(str(formats["bare_html"]))["result"]["text"]
    assert "no paragraph around them" in text


def test_html_probe_reports_no_ruling_lines_section(formats):
    """A format whose tables are markup has no ruling lines to sample.

    Reporting `pages_with_ruling_lines: 0` would read as "no tables here",
    which is a different claim and a false one -- the fixture has a table.
    """
    result = read.probe(str(formats["article_html"]))["result"]
    assert "tables" not in result
    assert read.probe(str(formats["book_epub"]))["result"].get("tables") is None


# --------------------------------------------------------------------------
# Markdown and text
# --------------------------------------------------------------------------


def test_markdown_outline_ignores_hashes_inside_a_fenced_block(formats):
    entries = read.outline(str(formats["readme_md"]))["result"]["entries"]
    titles = [e["title"] for e in entries]
    assert "Not a heading, this is a shell comment" not in titles
    assert titles == ["Project Title", "Second Level Heading", "Installation", "Notes"]


def test_markdown_setext_headings_are_found(formats):
    entries = read.outline(str(formats["readme_md"]))["result"]["entries"]
    by_title = {e["title"]: e["level"] for e in entries}
    assert by_title["Second Level Heading"] == 2
    assert by_title["Project Title"] == 1


def test_plain_text_declares_no_headings(formats):
    """A .txt file has no structure, and inventing one would be worse than none."""
    payload = read.outline(str(formats["plain_txt"]))
    _contract(payload, "outline")
    assert payload["result"]["count"] == 0
    assert payload["basis"] == "empty"
    assert "hint" in payload


def test_a_csv_with_classic_mac_line_endings_is_counted_correctly(formats, tmp_path):
    """`count("\\n")` reports 1 line for a file that has 8,000 of them.

    A real 12 MB export in the corpus uses \\r endings exclusively. The CSV
    reader parsed it into 8,001 rows while the same response said `lines: 1` --
    two fields of one answer contradicting each other.
    """
    odd = tmp_path / "mac.csv"
    odd.write_bytes(b"a,b\r1,2\r3,4\r")
    details = read.probe(str(odd))["result"]["format_details"]
    assert details["lines"] == 3
    assert details["rows"] == 3


def test_a_quoted_newline_does_not_crash_the_csv_reader(formats, tmp_path):
    """StringIO translates newlines, and csv.reader then REFUSES the file.

        _csv.Error: new-line character seen in unquoted field

    It escaped probe() entirely, so a tool that owes every caller a dict raised
    instead. Found on a real export whose article text spans lines inside its
    quotes -- not a constructed edge case.
    """
    odd = tmp_path / "quoted.csv"
    odd.write_bytes(b'id,text\r\n1,"line one\r\nline two"\r\n2,plain\r\n')
    payload = read.probe(str(odd))
    _contract(payload, "probe")
    assert payload["success"], payload.get("error")
    rows = read.extract_tables(str(odd), pages="1")["result"]["tables"][0]["rows"]
    assert len(rows) == 3, rows
    assert "\n" in rows[1][1], "the quoted line break belongs inside its cell"


def test_csv_quoted_fields_survive_the_delimiter_inside_them(formats):
    """Splitting on commas shifts the columns and produces data that is WRONG.

    The fixture has a quoted comma and a quoted newline; naive splitting gives
    four columns on one row and a phantom fifth row from the embedded newline.
    """
    result = read.extract_tables(str(formats["sales_csv"]))["result"]
    assert result["count"] == 1
    rows = result["tables"][0]["rows"]
    assert len(rows) == 4, rows
    assert all(len(row) == 3 for row in rows), rows
    assert rows[1] == ["North", "Strong, steady growth", "412"]
    assert "\n" in rows[2][1], "the quoted newline should stay inside its cell"


# --------------------------------------------------------------------------
# Office
# --------------------------------------------------------------------------


def test_docx_keeps_a_table_where_the_document_puts_it(formats):
    """python-docx exposes paragraphs and tables as two flat lists.

    Reading them in that order puts every table at the end of the document --
    a result that opens, reads as English, and is wrongly ordered.
    """
    text = read.extract(str(formats["report_docx"]))["result"]["text"]
    before = text.find("before the table")
    table = text.find("Region")
    after = text.find("after the table")
    assert -1 < before < table < after, text


def test_docx_headings_come_from_styles_not_type_size(formats):
    payload = read.outline(str(formats["report_docx"]))
    entries = payload["result"]["entries"]
    assert [(e["level"], e["title"]) for e in entries] == [(1, "Annual Report"), (2, "Figures")]
    assert payload["basis"] == "native"


def test_pptx_reads_a_slide_in_visual_order_not_shape_order(formats):
    """Shape order is z-order: the order shapes were added, not laid out.

    The fixture adds the body box before the title box, so a reader trusting
    shape index reads the body first.
    """
    text = read.read_page(str(formats["deck_pptx"]), 2)["result"]["text"]
    assert text.find("Second Slide Heading") < text.find("Body text placed lower")


def test_pptx_speaker_notes_are_not_lost(formats):
    """Notes carry no box on the slide, and reading order used to drop them.

    `order_blocks` returned only blocks WITH geometry whenever any block had
    it, so every speaker note in every deck vanished from read_page and extract
    while both reported success. Notes are frequently where a deck's actual
    content lives.
    """
    text = read.read_page(str(formats["deck_pptx"]), 1)["result"]["text"]
    assert "revised timetable" in text


def test_pptx_one_page_per_slide(formats):
    result = read.probe(str(formats["deck_pptx"]))["result"]
    assert result["pages"] == 3
    assert result["format_details"]["slides"] == 3


def test_xlsx_one_page_per_sheet_named_in_order(formats):
    result = read.probe(str(formats["book_xlsx"]))["result"]
    assert result["pages"] == 2
    assert result["format_details"]["sheets"] == ["Revenue", "Notes"]


def test_xlsx_a_stray_far_cell_does_not_inflate_the_row_count(formats):
    """`sheet.max_row` counts formatting, not data.

    One touched cell at Z400 makes a four-row sheet report 400 rows, and every
    count derived from it is then a hundredfold overstatement.
    """
    result = read.extract_tables(str(formats["book_xlsx"]), pages="1")["result"]
    rows = result["tables"][0]["rows"]
    assert len(rows) == 4, rows


def test_xlsx_maps_every_sheet_to_the_pages_it_occupies(formats):
    """Sheet names and page numbers are useless to a caller unless connected."""
    details = read.probe(str(formats["book_xlsx"]))["result"]["format_details"]
    assert details["sheet_pages"] == {"Revenue": "1", "Notes": "2"}


def test_a_sheet_too_large_for_one_page_is_divided_and_says_so(formats, monkeypatch):
    """One sheet, one page produced a page nobody could read.

    A real export in the corpus is a single 312,000-token sheet. As one page,
    extract refused it for exceeding the budget and told the caller to narrow
    the page range -- of a one-page document. The refusal was unactionable,
    which is worse than not offering the tool.

    Forced here with a tiny page size rather than by shipping a huge fixture.
    """
    monkeypatch.setenv("DOCS_FLOW_CHARS_PER_PAGE", "40")
    result = read.probe(str(formats["book_xlsx"]))["result"]
    assert result["pages"] > 2, "a 40-character page should divide these sheets"
    # The division is ours, not the workbook's, and the response says which.
    assert result["pagination"] == "synthetic"
    # And every page is individually readable, which was the whole point.
    payload = read.extract(str(formats["book_xlsx"]), pages="1")
    assert payload["success"] and payload["result"]["text"].strip()


def test_xlsx_returns_values_not_formula_text(formats):
    """A caller asking a document server what a spreadsheet says wants the number."""
    text = read.extract(str(formats["book_xlsx"]), pages="1")["result"]["text"]
    assert "=SUM" not in text


# --------------------------------------------------------------------------
# epub and email
# --------------------------------------------------------------------------


def test_epub_reads_chapters_in_spine_order(formats):
    """The spine is the publisher's statement of reading order.

    The fixture's archive holds the chapters as c3, c1, c2. Manifest order,
    archive order and an alphabetical sort all produce a readable book with its
    chapters shuffled -- plausible and wrong.
    """
    titles = [read.read_page(str(formats["book_epub"]), n)["result"]["text"].splitlines()[0] for n in (1, 2, 3)]
    assert titles == ["Chapter One", "Chapter Two", "Chapter Three"]


def test_epub_metadata_comes_from_the_package_document(formats):
    details = read.probe(str(formats["book_epub"]))["result"]["format_details"]
    assert details["title"] == "A Short Book"
    assert details["author"] == "R. Fenner"
    assert details["spine_items"] == 3


def test_eml_prefers_the_plain_body_and_returns_it_once(formats):
    text = read.extract(str(formats["message_eml"]))["result"]["text"]
    assert "board meets on Thursday" in text
    assert "HTML ALTERNATIVE BODY" not in text, "both alternatives returned: double the tokens for one message"


def test_eml_decodes_an_encoded_word_subject(formats):
    details = read.probe(str(formats["message_eml"]))["result"]["format_details"]
    assert details["subject"] == "Quarterly review attached"


def test_eml_lists_attachments_without_extracting_them(formats, tmp_path):
    details = read.probe(str(formats["message_eml"]))["result"]["format_details"]
    assert details["attachment_count"] == 1
    text = read.extract(str(formats["message_eml"]))["result"]["text"]
    assert "figures.csv" in text
    # A read tool must not write files as a side effect.
    assert not list(tmp_path.iterdir())


def test_eml_falls_back_to_html_when_there_is_no_plain_part(formats):
    text = read.extract(str(formats["html_only_eml"]))["result"]["text"]
    assert "statement for July is available" in text
    assert "var t = 1" not in text


def test_outlook_msg_is_refused_with_the_reason(tmp_path):
    """.msg is an OLE compound file, not RFC 5322. Parsing it yields nonsense."""
    fake = tmp_path / "mail.msg"
    fake.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    payload = read.probe(str(fake))
    _contract(payload, "probe")
    assert not payload["success"]
    assert "Outlook" in payload["error"]
    assert ".eml" in payload["hint"]


# --------------------------------------------------------------------------
# The edit tier is PDF-only, and says so by name
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda p: edit.optimize(p, action="compress"),
        lambda p: edit.protect(p, action="encrypt", password="x"),
        lambda p: edit.redact(p, pattern="Revenue"),
        lambda p: edit.ocr(p),
        lambda p: edit.assemble([p], select="a:all", out="out.pdf"),
    ],
)
def test_pdf_only_tools_refuse_another_format_with_a_next_step(formats, call):
    """A refusal naming convert(to='pdf') beats a library error from three frames down.

    ocr() is the one that mattered: it opened the file through the reader layer
    first, where an HTML file opens FINE, then reported every page as already
    having a text layer -- success, and wrong.
    """
    payload = call(str(formats["article_html"]))
    assert not payload["success"], payload
    assert payload["hint"], "a refusal with no next step"
    assert "convert" in payload["hint"] or "pdf" in payload["hint"].lower()
    assert isinstance(payload["token_estimate"], int)


@pytest.mark.parametrize("name", ["article_html", "report_docx", "book_epub", "message_eml", "readme_md"])
def test_convert_to_text_works_for_every_readable_format(formats, tmp_path, name):
    """convert() goes through the reader layer, so it is not PDF-only.

    It refused every non-PDF source until the readers existed, which was a
    leftover restriction rather than a limitation -- `_from_source` never held
    anything PDF-specific, it calls the docs-read tools and they route by
    extension.
    """
    out = tmp_path / f"{name}.md"
    payload = edit.convert(str(formats[name]), to="md", out=str(out))
    _contract(payload, "convert")
    assert payload["success"], payload.get("error")
    assert out.exists() and out.read_text().strip()
    assert payload["result"]["characters"] == len(out.read_text())


def test_convert_to_images_still_requires_a_pdf(formats, tmp_path):
    """Rendering goes through PDFium; an email has nothing to rasterise."""
    payload = edit.convert(str(formats["message_eml"]), to="images", out=str(tmp_path / "pages"))
    _contract(payload, "convert")
    assert not payload["success"]
    assert "to='pdf'" in payload["hint"]


# --------------------------------------------------------------------------
# Budgets apply to every format, not only PDF
# --------------------------------------------------------------------------


def test_a_file_over_the_source_limit_is_refused_before_parsing(formats, tmp_path, monkeypatch):
    """A flow parser builds the whole tree or nothing, so the door is the only place.

    Refused by stat, not by reading: the point is to answer without paying the
    cost being refused.
    """
    monkeypatch.setenv("DOCS_MAX_SOURCE_BYTES", "500")
    payload = read.probe(str(formats["long_html"]))
    _contract(payload, "probe")
    assert not payload["success"]
    assert "MB" in payload["error"]
    assert "convert" in payload["hint"] or "Split" in payload["hint"]


def test_extract_still_refuses_over_the_token_budget_for_a_flow_format(formats, monkeypatch):
    monkeypatch.setenv("DOCS_MAX_RESPONSE_TOKENS", "50")
    payload = read.extract(str(formats["long_html"]))
    _contract(payload, "extract")
    assert not payload["success"]
    assert payload["limit"] and payload["seen"]
    assert "pages=" in payload["hint"]
