"""Table reconstruction, and honest confidence about it.

Two strategies, and the response always says which ran:

    ruled       ruling lines are present. High confidence -- the grid is in
                the file, not inferred.
    whitespace  column boundaries clustered from gaps. Genuinely uncertain,
                and must not be reported as if it were the first.

The pair is the whole point. Measured on the corpus, pdfplumber's line strategy
finds the ruled fixture's table and finds nothing in the unruled one; the text
strategy finds a table in the unruled fixture and gets its shape wrong -- 9 rows
where there are 5. That error is not a bug to fix, it is the accuracy of the
method, and the only defensible response is to report it as low confidence
rather than to launder it into a number that looks like the ruled case.

pdfplumber reports a stroked rectangle under `rects`, not `lines`: a table
drawn as one `re S` per cell has lines=0 and rects=15. Checking only `lines`
reports every such table as unruled.
"""

from __future__ import annotations

from typing import Any

from core.ir import Basis

RULED_SETTINGS: dict[str, Any] = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
WHITESPACE_SETTINGS: dict[str, Any] = {"vertical_strategy": "text", "horizontal_strategy": "text"}

# What a reconstruction is worth. Not a measurement -- there is no ground truth
# at run time -- but a stable statement of method quality, so a caller can
# filter on it. The ruled figure is high because the grid came from the file.
CONFIDENCE = {"ruled": 0.95, "whitespace": 0.5}


def extract_page_tables(plumbed_page, prefer_ruled: bool = True) -> list[dict]:
    """Tables on one pdfplumber page, each carrying how it was found.

    Ruled first: if the page has a real grid, the whitespace strategy would
    only be a worse answer to the same question. Falling back rather than
    running both also keeps a page from reporting the same table twice under
    two bases, which would be one table and two confidences.
    """
    found: list[dict] = []
    if prefer_ruled and (plumbed_page.lines or plumbed_page.rects or plumbed_page.edges):
        found = _run(plumbed_page, RULED_SETTINGS, "ruled")
    if not found:
        found = _run(plumbed_page, WHITESPACE_SETTINGS, "whitespace")
    return found


def _run(plumbed_page, settings: dict, basis: Basis) -> list[dict]:
    out: list[dict] = []
    try:
        tables = plumbed_page.find_tables(settings)
    except Exception:
        # pdfplumber raises from deep inside pdfminer on malformed pages. A
        # page whose tables cannot be read is not a failed call -- the other
        # pages still have answers -- so this degrades to "no tables here".
        return out

    for table in tables:
        rows = [[(cell or "").strip() for cell in row] for row in table.extract()]
        rows = [row for row in rows if any(cell for cell in row)]
        if not rows:
            continue
        entry: dict[str, Any] = {
            "page": plumbed_page.page_number,
            "rows": rows,
            "shape": [len(rows), max(len(r) for r in rows)],
            "basis": basis,
            "confidence": CONFIDENCE[basis],
        }
        if basis == "whitespace":
            entry["note"] = "column boundaries inferred from gaps; verify the shape before use"
        out.append(entry)
    return out
