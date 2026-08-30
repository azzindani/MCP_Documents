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

    # Read the words once per page, not once per table, and only when there is
    # a table to fill -- extract_words() costs about as much as find_tables()
    # itself, and paying it on every page of a 500-page document that has no
    # table on it made the suite six times slower.
    if not tables:
        return out

    # BOTH strategies fill cells from whole words. This was applied to the
    # whitespace path only, on the reasoning that a ruled table's rules sit
    # between cells so no word can straddle one. Real documents disagree: on a
    # supreme-court judgment the ruled header cell came back as
    # `Ditambah/(Dikurangi)Keberata` while the row band plainly contains
    # `Keberatan` — pdfplumber assigns characters by position and clips at the
    # cell edge whichever strategy found the cell, so a value that overhangs
    # its own rule is cut. High confidence made it worse, not better: 0.95 on a
    # word missing its last letter.
    words = plumbed_page.extract_words()

    for table in tables:
        rows = _rows_from_words(table, words)
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
            entry["note"] = (
                "column boundaries inferred from gaps; verify the shape before use. "
                "Cell values are whole words, and content outside the inferred grid is not included."
            )
        out.append(entry)
    return out


def _rows_from_words(table, words: list[dict]) -> list[list[str]]:
    """Fill each cell with the WHOLE words whose centre falls inside it.

    pdfplumber's own `extract()` assigns characters by position and clips at
    the table's bounding box, so a value that crosses the boundary comes back
    cut in half. On a LibreOffice-produced page whose inferred table bbox ended
    at x=185.6 while `1450.50` runs from 152.8 to 202.3, the cell read
    `1450.` -- a number silently missing its last two digits, returned as a
    successful extraction.

    That is a different failure from the one this module already accepts. The
    whitespace strategy's SHAPE is a guess and says so; a truncated value is
    not a guess about structure, it is a wrong number, and no confidence score
    warns a caller that the digits are missing.

    The centre is the test rather than full containment, because a word that
    straddles a boundary belongs to the column it mostly sits in, and requiring
    containment would drop it entirely -- trading a cut value for a missing one.
    """
    rows: list[list[str]] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            if cell is None:
                cells.append("")
                continue
            x0, top, x1, bottom = cell
            inside = [
                word
                for word in words
                if x0 <= (word["x0"] + word["x1"]) / 2 <= x1 and top <= (word["top"] + word["bottom"]) / 2 <= bottom
            ]
            inside.sort(key=lambda word: word["x0"])
            cells.append(" ".join(word["text"] for word in inside).strip())
        rows.append(cells)
    return rows
