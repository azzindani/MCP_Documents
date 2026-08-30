"""Real documents, referenced where they live -- never copied into this repo.

Synthetic fixtures prove a specific thing on purpose. Real documents prove the
thing nobody thought to build: a 602-page federal regulation whose every second
page from page 8 is a scanned plate, a financial statement that is 183 pages of
text with one scanned signature page, an invoice that is a photograph.

This module POINTS at them. It does not copy them, for three reasons:

  * This repo is public and those documents are not the repo's to publish.
  * They are 1-14 MB each; a corpus of them is a repository nobody can clone.
  * A copy goes stale silently. A reference is either there or it is not, and
    the tests say which.

Every test built on these SKIPS when the corpus is absent, so CI (which has no
such directory) stays green and a local run gets the harder evidence. Point
DOCS_REAL_CORPUS somewhere else to use a different set.

The characteristics below were measured with probe(), not assumed -- which is
the only reason the scanned ones are known to be scanned. `invoice_IN-105.pdf`
looks like an invoice and contains zero extractable characters.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("DOCS_REAL_CORPUS", "/root/Evals"))

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
    """Absolute path to a named real document, or skip the calling test."""
    relative, _ = DOCUMENTS[name]
    candidate = ROOT / relative
    if not candidate.exists():
        pytest.skip(f"real corpus absent: {candidate} (set DOCS_REAL_CORPUS)")
    return candidate


def available() -> bool:
    return ROOT.is_dir()


requires_real = pytest.mark.skipif(not available(), reason=f"real corpus absent at {ROOT}")
