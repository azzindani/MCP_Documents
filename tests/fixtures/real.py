"""Real documents: one committed, the rest referenced where they live.

Synthetic fixtures prove a specific thing on purpose. Real documents prove the
thing nobody thought to build: a 602-page federal regulation whose every second
page from page 8 is a scanned plate, a financial statement that is 183 pages of
text with one scanned signature page, an invoice that is a photograph.

Most of them are POINTED at rather than copied, for three reasons:

  * This repo is public and those documents are not the repo's to publish.
  * They are 1-14 MB each; a corpus of them is a repository nobody can clone.
  * A copy goes stale silently. A reference is either there or it is not, and
    the tests say which.

Tests built on those SKIP when the corpus is absent, so CI (which has no such
directory) stays green and a local run gets the harder evidence. Point
DOCS_REAL_CORPUS somewhere else to use a different set.

**One document is committed anyway, in `real_corpus/`.** Skipping is the right
default and it is also how a whole class of defect stayed invisible: the four
worst faults this server has had were found by a real document, and every test
that could have caught them was a skip in CI. The BBCA filing is committed so
that at least one 183-page real document is exercised on every push, on three
platforms, by a machine that is not this one.

It is committed because it can be: it is a public IDX filing, not a customer's
file, and its owner authorised it. Two files, 2.7 MB:

  * the PDF -- 183 pages, three different page geometries including landscape,
    one scanned signature page, and tables of nine-digit figures.
  * `IDX_BBCA_Q1_2026_instance.zip` -- the XBRL of the SAME filing, which is
    the point. It carries 586 numeric facts machine-readably, so a test can
    assert that a number this server read out of the PDF matches the number
    the issuer filed, rather than asserting that extraction returned something.
    An oracle produced by a different tool chain is the only kind worth having.

The characteristics below were measured with probe(), not assumed -- which is
the only reason the scanned ones are known to be scanned. `invoice_IN-105.pdf`
looks like an invoice and contains zero extractable characters.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("DOCS_REAL_CORPUS", "/root/Evals"))

# Committed into the repo, so these never skip -- not even in CI.
COMMITTED = Path(__file__).parent / "real_corpus"

# name -> (relative path, what it is here to prove)
DOCUMENTS: dict[str, tuple[str, str]] = {
    # Fully scanned: zero extractable characters behind a document that looks
    # ordinary. extractable must be "none" and every page must read as scanned.
    "scanned_invoice": ("corporate_finance/invoice_IN-105.pdf", "1 page, no text layer at all"),
    "scanned_invoice_2p": ("corporate_finance/invoice_IN-263.pdf", "2 pages, no text layer"),
    "scanned_resume": ("recruitment/resume_95.pdf", "a resume that is a photograph"),
    # Born-digital, ordinary. The control for everything above.
    "digital_invoice": ("corporate_finance/invoice_IN-11664.pdf", "1 page, clean text layer"),
    "contract": (
        "legal/contract_ZogenixInc_20190509_10-Q_EX-10.2_11663146_EX-10.2_Consulting Agreement.pdf",
        "68 pages of dense legal prose",
    ),
    # Hybrids: the case a single per-document verdict cannot describe.
    # Committed (see COMMITTED above), so this one alone never skips.
    "hybrid_financial": (
        "investment/IDX_BBCA_Q1_2026_Financial_Statement.pdf",
        "183 pages, page 2 is a scanned signature page",
    ),
    # Big. The budget refusals are untestable on anything smaller, and this is
    # larger and messier than the synthetic 500-page fixture.
    "huge_regulation": ("regulation/us_gov_CFR-2023-title40-vol11.pdf", "843 pages, ~910k tokens"),
    "huge_hybrid": (
        "regulation/us_gov_CFR-2025-title7-vol2.pdf",
        "602 pages with scanned plates interleaved from page 8",
    ),
    # Non-PDF, for the readers that are not the PDF reader.
    "html_report": ("report/orca_report.html", "a real 3.9 MB HTML report"),
    "workbook": ("dataframe/Coffee_Shop_Sales.xlsx", "a real spreadsheet"),
}


def path(name: str) -> Path:
    """Absolute path to a named real document, or skip the calling test.

    The committed copy wins over the referenced one. Not a fallback for when
    the corpus is missing -- the priority is deliberate, so that a test asserting
    an exact figure reads the same bytes on every machine and on all three CI
    runners. Pointing DOCS_REAL_CORPUS at a different set would otherwise change
    what "the BBCA filing" means underneath an assertion about its page 5.
    """
    relative, _ = DOCUMENTS[name]
    committed = COMMITTED / Path(relative).name
    if committed.exists():
        return committed
    candidate = ROOT / relative
    if not candidate.exists():
        pytest.skip(f"real corpus absent: {candidate} (set DOCS_REAL_CORPUS)")
    return candidate


def xbrl_facts() -> dict[str, int]:
    """The BBCA filing's own numbers, in millions, from its XBRL instance.

    The independent oracle: same filing, same issuer, different tool chain.
    Keyed `FactName|contextRef`, holding every fact of a million or more --
    which is the magnitude a rupiah financial statement is written in, and the
    magnitude where a truncated or mis-parsed digit stops being visible to a
    human reader of the extracted text.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    archive = COMMITTED / "IDX_BBCA_Q1_2026_instance.zip"
    if not archive.exists():
        pytest.skip(f"XBRL oracle absent: {archive}")
    with zipfile.ZipFile(archive) as zf:
        with zf.open("instance.xbrl") as handle:
            root = ET.parse(handle).getroot()

    facts: dict[str, int] = {}
    for element in root.iter():
        context = element.get("contextRef")
        text = (element.text or "").strip()
        if not context or not text:
            continue
        # Integers only, and only ones large enough to be a rupiah figure. A
        # ratio like 0.0712 is a fact too, but it is not what the PDF's tables
        # print and matching it would be a different test.
        if not text.lstrip("-").isdigit() or abs(int(text)) < 1_000_000:
            continue
        name = element.tag.rsplit("}", 1)[-1]
        facts[f"{name}|{context}"] = int(text) // 1_000_000
    return facts


def available() -> bool:
    return ROOT.is_dir()


requires_real = pytest.mark.skipif(not available(), reason=f"real corpus absent at {ROOT}")
