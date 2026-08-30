"""Turning extracted text into text somebody can use.

This is the difference between 300 usable pages and 300 pages of noise, and
none of it is done for you by any PDF library. Four steps, in order, each
individually disableable, and **each reporting what it changed** -- a cleaner
that silently rewrites text is indistinguishable from a corrupt extraction, so
the counts are what make the edits reviewable.

Measured against the corpus, running heads alone are one line in eight of a
real government document: every page of a CFR volume carries
`Pt. 63    40 CFR Ch. I (7-1-23 Edition)`, and every page of the running_heads
fixture carries `CONFIDENTIAL - Acme Corporation - Internal Use Only`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# A line must appear on at least this fraction of pages to be furniture rather
# than content. Deliberately below the obvious 0.9: page numbers vary, so
# "Page 3 of 40" never repeats, and what repeats is the surrounding text on a
# subset of pages -- section headers change at chapter boundaries, and a front
# matter run of ten pages carries a different head from the body.
RUNNING_HEAD_FRACTION = 0.6

# How many lines at each edge may be furniture. ONE, deliberately.
#
# This was 12% of the page, which on a 30-line page makes three lines at each
# edge candidates -- and the first body line of every page is then a candidate.
# Combined with _shape() blanking digits, `Body sentence 1 on page 1.` and
# `Body sentence 1 on page 2.` became one repeated shape, got promoted to
# furniture, and took 24 body lines with them.
#
# A running head is the topmost line and a running foot the bottom-most. A
# two-line header loses its second line here, which is the right way to be
# wrong: leaving furniture in is a cosmetic flaw, deleting body text is a
# silent corruption reported as success.
EDGE_LINES = 1

# The minimum pages before repetition means anything. On three pages, one
# repeated line is 33% by accident.
MIN_PAGES_FOR_HEADS = 4

_LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}
_SOFT_HYPHEN = "­"
_NBSP = " "

# A hyphen at end of line, preceded by a letter. Captures the stem so the join
# can be judged rather than performed blindly.
_HYPHEN_BREAK = re.compile(r"(\w+)-$")
_ENDS_SENTENCE = re.compile(r"[.!?][\"')\]]?$")


def normalise_unicode(text: str) -> tuple[str, int]:
    """Ligatures, soft hyphens and non-breaking spaces out. Returns the count.

    `ﬁ` is one character in a PDF and two in every search the caller will run,
    so a document containing "identiﬁcation" does not match "identification"
    and the caller concludes the word is absent. Soft hyphens are invisible and
    break matching the same way.
    """
    changed = 0
    for ligature, plain in _LIGATURES.items():
        if ligature in text:
            changed += text.count(ligature)
            text = text.replace(ligature, plain)
    if _SOFT_HYPHEN in text:
        changed += text.count(_SOFT_HYPHEN)
        text = text.replace(_SOFT_HYPHEN, "")
    if _NBSP in text:
        changed += text.count(_NBSP)
        text = text.replace(_NBSP, " ")
    # NFKC after the explicit table, not instead of it: it would fold the
    # ligatures too, but it also rewrites things the caller may care about
    # (superscripts to digits, full-width forms), so the counted, reversible
    # replacements happen first and this only tidies what is left.
    normalised = unicodedata.normalize("NFC", text)
    return normalised, changed


def join_hyphenated(lines: list[str]) -> tuple[list[str], int]:
    """Join `manu-` + `facturing` into `manufacturing`. Returns the count.

    Only where the hyphen ends a line AND the next line starts lowercase. A
    line ending in `state-of-the-art` mid-sentence keeps its hyphen, and so
    does `COVID-` followed by `19` -- the uppercase start says the hyphen was
    real rather than a line-break artefact.

    Works across a page boundary too, because the caller passes the lines of
    every page it asked for as one list. A per-page cleaner cannot fix
    `recon-` on the last line of page 1 and `sider` on the first of page 2,
    which is the case the hyphenated fixture exists for.
    """
    out: list[str] = []
    joined = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _HYPHEN_BREAK.search(line.rstrip())
        # Look past a single blank line. clean_pages inserts one at every page
        # boundary, and the whole reason join_hyphenated takes all the pages at
        # once is the word broken ACROSS a page break -- which the first
        # version then failed to join, because the very separator that made the
        # cross-page case reachable was sitting between the two halves.
        skip = 0
        following = ""
        for ahead in (1, 2):
            if index + ahead < len(lines):
                candidate = lines[index + ahead]
                if candidate.strip():
                    following, skip = candidate, ahead
                    break
                if ahead == 2:
                    break
        if match and following and following[:1].islower():
            out.append(line.rstrip()[:-1] + following.lstrip())
            joined += 1
            index += skip + 1
            continue
        out.append(line)
        index += 1
    return out, joined


def find_running_heads(pages_lines: dict[int, list[str]]) -> set[str]:
    """Lines that repeat near the top or bottom of most pages.

    Position matters as much as repetition: a line has to be near an edge to be
    furniture. Without that, a repeated body line -- a standard clause, a table
    header, "Continued on next page" -- is deleted from the middle of the text,
    which is a far worse failure than leaving a header in.

    Page numbers defeat exact matching on their own, so a line is normalised by
    replacing digit runs with a placeholder before counting: `Page 3 of 40` and
    `Page 4 of 40` become one candidate.
    """
    if len(pages_lines) < MIN_PAGES_FOR_HEADS:
        return set()

    counts: Counter[str] = Counter()
    for lines in pages_lines.values():
        if not lines:
            continue
        candidates = lines[:EDGE_LINES] + lines[-EDGE_LINES:]
        counts.update({_shape(line) for line in candidates if line.strip()})

    threshold = len(pages_lines) * RUNNING_HEAD_FRACTION
    return {shape for shape, seen in counts.items() if seen >= threshold}


def _shape(line: str) -> str:
    """A line with digit runs blanked, so page numbers do not defeat matching."""
    return re.sub(r"\d+", "#", line.strip())


def strip_running_heads(lines: list[str], shapes: set[str]) -> tuple[list[str], int]:
    """Remove edge lines matching a known furniture shape. Returns the count.

    Only near the edges, mirroring find_running_heads exactly. Stripping the
    whole page instead destroyed the running_heads fixture completely -- 180 of
    180 lines removed, an empty document reported as a successful extraction.

    The cause is worth keeping: _shape() blanks digit runs so that `Page 3 of
    40` and `Page 4 of 40` are one candidate, and that is necessary. But it
    also makes `Body sentence 1 on page 1.` and `Body sentence 2 on page 2.`
    the same shape. Those collided at the edges, were promoted to furniture,
    and then matched every body line on every page.

    Detection has to look at the edges to be meaningful; removal has to look at
    exactly the same place, or it acts on evidence it did not gather.
    """
    if not shapes or not lines:
        return lines, 0
    edge = EDGE_LINES
    if len(lines) <= 2 * edge:
        head, body, tail = lines, [], []
    else:
        head, body, tail = lines[:edge], lines[edge:-edge], lines[-edge:]
    kept_head = [line for line in head if _shape(line) not in shapes]
    kept_tail = [line for line in tail if _shape(line) not in shapes]
    kept = kept_head + body + kept_tail
    return kept, len(lines) - len(kept)


def collapse_whitespace(lines: list[str]) -> list[str]:
    """Squeeze runs of spaces without destroying paragraph boundaries.

    Blank lines survive -- they are the only paragraph signal left once the
    geometry is gone -- but runs of more than one collapse to one.
    """
    out: list[str] = []
    blank = False
    for line in lines:
        squeezed = re.sub(r"[ \t]+", " ", line).strip()
        if not squeezed:
            if not blank:
                out.append("")
            blank = True
            continue
        blank = False
        out.append(squeezed)
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return out


def clean_pages(
    pages_lines: dict[int, list[str]],
    strip_heads: bool = True,
    join_hyphens: bool = True,
    normalise: bool = True,
) -> tuple[str, dict[str, object]]:
    """Run every step over a set of pages and report exactly what changed.

    Takes all the pages at once rather than one at a time, because two of the
    steps are inherently cross-page: furniture is only detectable by repetition
    ACROSS pages, and a hyphen can break across a page boundary.
    """
    # Counts stay a dict[str, int] so `+=` type-checks; the "could not check"
    # flag and its explanation are a separate mapping, merged on the way out.
    # Widening one dict to int | bool | str instead makes every increment an
    # error, which is the type system correctly objecting to a bag of mixed
    # things pretending to be a counter.
    counts: dict[str, int] = {"running_heads_removed": 0, "hyphens_joined": 0, "unicode_fixed": 0}
    notes: dict[str, object] = {}

    # Say when furniture detection did not run, rather than reporting zero
    # removals as though it had. Repetition across fewer than MIN_PAGES_FOR_HEADS
    # pages is not evidence -- on three pages one repeated line is 33% by
    # accident -- so a two-page extract keeps its header, and the response has
    # to distinguish "checked, nothing to remove" from "could not check".
    # A count of 0 beside an obviously present header is a claim the numbers do
    # not support, which is the defect shape this fleet finds most often.
    checkable = strip_heads and len(pages_lines) >= MIN_PAGES_FOR_HEADS
    if strip_heads and not checkable:
        notes["running_heads_checked"] = False
        notes["running_heads_note"] = f"needs at least {MIN_PAGES_FOR_HEADS} pages to tell furniture from body text"

    shapes = find_running_heads(pages_lines) if checkable else set()
    all_lines: list[str] = []
    for number in sorted(pages_lines):
        lines = pages_lines[number]
        if shapes:
            lines, removed = strip_running_heads(lines, shapes)
            counts["running_heads_removed"] += removed
        all_lines.extend(lines)
        all_lines.append("")  # page boundary, collapsed later

    if join_hyphens:
        all_lines, joined = join_hyphenated(all_lines)
        counts["hyphens_joined"] = joined

    text = "\n".join(collapse_whitespace(all_lines))
    if normalise:
        text, fixed = normalise_unicode(text)
        counts["unicode_fixed"] = fixed
    return text, {**counts, **notes}
