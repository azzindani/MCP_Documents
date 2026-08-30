"""Column detection and reading order.

Getting this wrong is the commonest way PDF text is silently garbage. A
two-column page read straight across interleaves every sentence with an
unrelated one, and the result looks like plausible English while meaning
nothing -- so it passes every eyeball check and every "did we get text" test.

Measured on the two_column fixture, naive extraction gives:

    LEFT p1 line 1 lorem | RIGHT p1 line 1 sit | LEFT p1 line 2 lorem | ...

which is why the fixture's two columns say different words. A wrong order is
legible instantly instead of looking like prose.

The algorithm is a gap histogram, not a model. Project every word box onto the
x axis, find vertical bands that no word crosses, and treat a band wide enough
and tall enough as a column separator. It is cheap, it is explainable, and when
it is unsure it says one column rather than guessing two -- a false single
column costs interleaving on that page; a false double column shreds a page
that was fine.
"""

from __future__ import annotations

from core.ir import Block, Page

# A gutter must be at least this wide, as a fraction of page width, to count as
# a column separator -- and this number was wrong until it was measured against
# a real document rather than reasoned about.
#
# It was 0.02, or 12.2pt on US Letter, chosen because inter-word space is 3-5pt.
# The real CFR volumes set their gutter at about 10pt, so every one of their
# pages was excluded by a hair: at 12pt the gutter column still shows 14 line
# crossings, and at 8pt it shows 2. The page was two columns, was detected as
# one, and read across into sentences that never existed.
#
# 0.013 is ~8pt on US Letter -- still well clear of word spacing, and below
# every gutter measured in the corpus. The value is coupled: _segments uses the
# same number to break a row, so lowering it also breaks rows at narrower gaps.
# That is harmless because spurious breaks land at different x on every row and
# only a gap consistent down the page survives the crossing threshold.
MIN_GUTTER_FRACTION = 0.013

# A gutter may be crossed by at most this fraction of the page's lines. Not
# zero: real two-column pages carry full-width running heads, spanning titles
# and rules, and demanding a completely uncrossed gutter reports every CFR page
# in the corpus as single-column. One header in forty lines is 2.5%.
MAX_GUTTER_CROSSING_FRACTION = 0.12

# Below these, column detection is not evidence, it is noise: a title page with
# six words has gutters everywhere, and four lines cannot establish a pattern.
MIN_WORDS_FOR_COLUMNS = 40
MIN_LINES_FOR_COLUMNS = 8

# A real column separator has substantial text on BOTH sides. Without this, one
# outlier line out in the left margin -- a section marker like `§63.161` sitting
# alone at x=18 while the body starts at x=120 -- opens a 114-point "gutter"
# between itself and the page, and the split then happens in the margin instead
# of between the columns. Measured on a real CFR page, which reported columns=2
# for entirely the wrong gap and stayed interleaved.
MIN_SIDE_ROW_FRACTION = 0.25

# More gutters than this means the page is a TABLE, not prose in columns.
# Documents are set in one, two or occasionally three columns; nothing is set
# in eight. Measured on a real consolidated statement of changes in equity,
# which reports 8 -- one gutter per numeric column. Reading a table
# column-by-column would be far more wrong than reading it row-major, so above
# this the page falls back to 1 and extract_tables() is the tool for it.
MAX_TEXT_COLUMNS = 3


def detect_columns(page: Page) -> int:
    """How many text columns this page has. Returns 1 when unsure.

    Counts, for each x, how many text LINES cross it, and calls a wide run of
    rarely-crossed x a gutter.

    The obvious implementation -- mark every x any word occupies, then look for
    a completely empty run -- does not survive real documents, and the CFR
    volumes in the corpus are why. Every page carries a full-width running head
    (`Pt. 63    40 CFR Ch. I (7-1-23 Edition)`) that spans both columns, so
    there is no empty x anywhere on the page and a plainly two-column page
    reports one. The interleaved output is legible in the extraction:

        63.468 Reporting requirements. 63.504 Additional requirements for perform-
        has sections connected by screws or In gas/vapor service means that a

    -- left column and right column glued into sentences that never existed.
    That reads as plausible English, which is exactly why it survives review.

    Counting crossings instead makes a single spanning line irrelevant: one
    header out of forty lines leaves the gutter crossed 2.5% of the time, well
    under the threshold, while a genuinely single-column page has its centre
    crossed by nearly every line.

    Still deliberately biased toward 1. A page wrongly called single-column
    reads in the wrong order; a page wrongly called two-column has its lines
    torn apart into two documents that never existed.
    """
    bands = count_gutters(page) + 1
    return bands if bands <= MAX_TEXT_COLUMNS else 1


def looks_tabular(page: Page) -> bool:
    """True when the page has more column gaps than any prose layout has.

    Reported separately from detect_columns rather than folded into it, because
    the caller needs to know WHY it got 1: a page of prose and a wide financial
    table both read row-major, and only one of them should be handed to
    extract_tables().
    """
    return count_gutters(page) + 1 > MAX_TEXT_COLUMNS


def count_gutters(page: Page) -> int:
    """How many column separators this page has."""
    return len(gutter_positions(page))


def gutter_positions(page: Page) -> list[tuple[int, int]]:
    """The x ranges of every column separator, left to right.

    Returns POSITIONS, not a count. order_blocks needs to know where the gutter
    actually is: splitting a two-column page at its horizontal midpoint instead
    assumes the columns are equal width and that no wide element shifts the
    extent, and on the real CFR volumes it does not hold -- the page was
    correctly detected as two columns and still came out interleaved,

        screws or In gas/vapor service means that a
        piece of equipment in organic haz- ductwork.

    because words landed in the wrong half of an equal split. Detection and
    splitting have to use the same boundary or the second undoes the first.
    """
    boxes = [b.bbox for b in page.blocks if b.bbox]
    if len(boxes) < MIN_WORDS_FOR_COLUMNS or page.width <= 0:
        return []

    min_gutter = max(2, int(page.width * MIN_GUTTER_FRACTION))
    rows = _rows(boxes)
    if len(rows) < MIN_LINES_FOR_COLUMNS:
        return []
    segments = [seg for row in rows for seg in _segments(row, min_gutter)]

    width = int(page.width) + 1
    crossings = [0] * width
    for x0, x1 in segments:
        for x in range(max(0, int(x0)), min(width, int(x1) + 1)):
            crossings[x] += 1

    left = min(int(b[0]) for b in boxes)
    right = max(int(b[2]) for b in boxes)
    max_crossings = len(rows) * MAX_GUTTER_CROSSING_FRACTION

    row_segments = [_segments(row, min_gutter) for row in rows]
    needed = max(2, int(len(rows) * MIN_SIDE_ROW_FRACTION))

    found: list[tuple[int, int]] = []
    run = 0
    for x in range(left, right + 1):
        if crossings[x] > max_crossings:
            if run >= min_gutter and _flanked(row_segments, x - run, x, needed):
                found.append((x - run, x))
            run = 0
        else:
            run += 1
    # A run reaching the right edge is the right margin, not a gutter: the loop
    # only credits a run that CLOSES against occupied space.
    return found


def _flanked(row_segments: list[list[tuple[float, float]]], start: int, end: int, needed: int) -> bool:
    """True when enough rows have text on BOTH sides of a candidate gutter.

    This is what separates a column separator from a wide margin. A margin has
    text on one side only; a gutter has a column on each. Counting rows rather
    than words keeps one very long line from standing in for a column.
    """
    left_rows = sum(1 for segs in row_segments if any(seg[1] <= start for seg in segs))
    right_rows = sum(1 for segs in row_segments if any(seg[0] >= end for seg in segs))
    return left_rows >= needed and right_rows >= needed


def _rows(
    boxes: list[tuple[float, float, float, float]], tolerance: float = 2.0
) -> list[list[tuple[float, float, float, float]]]:
    """Group word boxes into rows sharing a baseline, within a tolerance.

    Exact y comparison puts every word on its own row: baselines of one visual
    line differ by fractions of a point.
    """
    rows: list[list[tuple[float, float, float, float]]] = []
    current: list[tuple[float, float, float, float]] = []
    last_y: float | None = None
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        if last_y is not None and abs(box[1] - last_y) <= tolerance:
            current.append(box)
        else:
            if current:
                rows.append(current)
            current = [box]
        last_y = box[1]
    if current:
        rows.append(current)
    return rows


def _segments(row: list[tuple[float, float, float, float]], min_gutter: int) -> list[tuple[float, float]]:
    """Split one row into runs of words, breaking at any gap a gutter could fit.

    This is the half that the first two attempts got wrong, in opposite
    directions. Marking every x a word occupies finds a gutter in every
    inter-word space. Taking each row's full extent from its leftmost to its
    rightmost word does the reverse: on a two-column page whose columns share
    exact baselines -- which is what a synthetically generated fixture produces,
    and what real typesetting sometimes does -- both columns merge into one
    full-width row and the gutter disappears.

    So a row is broken wherever the gap between consecutive words is at least
    as wide as the narrowest thing that could BE a gutter. The two thresholds
    are then the same number by construction, which is the property that makes
    this stable: a gap too small to be a gutter cannot break a row, and a gap
    big enough to break a row is by definition big enough to be one.

    Spurious breaks from wide justified spacing are harmless because they land
    at different x on every row -- only a gap consistent DOWN the page survives
    the crossing threshold.
    """
    ordered = sorted(row, key=lambda b: b[0])
    segments: list[tuple[float, float]] = []
    start, end = ordered[0][0], ordered[0][2]
    for x0, _, x1, _ in ordered[1:]:
        if x0 - end >= min_gutter:
            segments.append((start, end))
            start = x0
        end = max(end, x1)
    segments.append((start, end))
    return segments


def order_blocks(page: Page, columns: int | None = None) -> list[Block]:
    """Sort blocks into reading order: column by column, top to bottom.

    Blocks with no geometry keep the order the reader produced -- for a plain
    text file or an email body, that order IS the reading order, and inventing
    coordinates to sort by would be worse than leaving it alone.
    """
    positioned = [b for b in page.blocks if b.bbox]
    if not positioned:
        return list(page.blocks)

    # Blocks with no geometry on a page where others HAVE it are kept, at the
    # end, in the reader's order. Returning only the positioned ones dropped
    # them silently: a slide's speaker notes carry no box on the slide, so
    # every note in a deck vanished from read_page and extract while the tool
    # reported success. Nothing can sort them against a coordinate, but "we do
    # not know where this goes" is not a reason to lose it.
    unpositioned = [b for b in page.blocks if not b.bbox]

    count = columns if columns is not None else detect_columns(page)
    if count <= 1:
        ordered = sorted(positioned, key=lambda b: (round(b.bbox[1], 1), b.bbox[0]))  # type: ignore[index]
        ordered.extend(unpositioned)
        for i, block in enumerate(ordered):
            block.order, block.column = i, 0
        return ordered

    # Split at the MEASURED gutters, not at equal fractions of the page. The
    # equal-width version detected the CFR volumes as two columns and still
    # interleaved them, because their columns are not the same width and the
    # running head widens the extent.
    gutters = gutter_positions(page)
    if len(gutters) + 1 != count:
        # Caller overrode the count, or the page changed under us. Fall back to
        # equal widths rather than pretending to know where the boundaries are.
        left = min(b.bbox[0] for b in positioned)  # type: ignore[index]
        right = max(b.bbox[2] for b in positioned)  # type: ignore[index]
        step = (right - left) / count or 1.0
        cuts = [left + (i + 1) * step for i in range(count - 1)]
    else:
        cuts = [(start + end) / 2 for start, end in gutters]

    ordered: list[Block] = []
    for index in range(count):
        lo = cuts[index - 1] if index else float("-inf")
        hi = cuts[index] if index < len(cuts) else float("inf")
        in_column = [b for b in positioned if lo <= ((b.bbox[0] + b.bbox[2]) / 2) < hi]  # type: ignore[index]
        for block in sorted(in_column, key=lambda b: (round(b.bbox[1], 1), b.bbox[0])):  # type: ignore[index]
            block.column = index
            block.order = len(ordered)
            ordered.append(block)
    for block in unpositioned:
        block.column = count - 1
        block.order = len(ordered)
        ordered.append(block)
    return ordered


def lines_from_blocks(blocks: list[Block], tolerance: float = 2.0) -> list[str]:
    """Join word-level blocks back into lines, using their y positions.

    load_page_words gives one block per word. Text is wanted as lines, and
    "same line" means "same y within a tolerance" rather than "equal y":
    baselines of the same visual line differ by fractions of a point, and
    exact comparison puts every word on its own line.
    """
    if not blocks:
        return []
    if not any(b.bbox for b in blocks):
        return [b.text for b in blocks]

    lines: list[str] = []
    current: list[Block] = []
    last_y: float | None = None
    for block in blocks:
        y = block.bbox[1] if block.bbox else 0.0
        if last_y is None or abs(y - last_y) <= tolerance:
            current.append(block)
        else:
            lines.append(" ".join(b.text for b in current))
            current = [block]
        last_y = y
    if current:
        lines.append(" ".join(b.text for b in current))
    return lines


def lines_with_size(blocks: list[Block], tolerance: float = 2.0) -> list[tuple[str, float]]:
    """Like lines_from_blocks, but each line carries its largest font size.

    Heading detection needs a size per line, and the flat list of every span's
    size that to_markdown first used could only answer "does this document
    contain a large font somewhere" -- which is not a question about any line.
    """
    if not blocks:
        return []
    # The guard lines_from_blocks carries, which this twin was missing. A
    # format with no geometry gives every block y=0.0, so the tolerance test
    # below collapsed an ENTIRE page onto one line: to_markdown of an HTML file
    # with two headings and a table came back as one run-together paragraph
    # reporting `headings: 0` and success: true, while outline() of the same
    # file listed both headings correctly. Where there are no coordinates the
    # reader's order IS the reading order, so one block is one line.
    if not any(b.bbox for b in blocks):
        return [_line_of([block]) for block in blocks]

    out: list[tuple[str, float]] = []
    current: list[Block] = []
    last_y: float | None = None
    for block in blocks:
        y = block.bbox[1] if block.bbox else 0.0
        if last_y is None or abs(y - last_y) <= tolerance:
            current.append(block)
        else:
            out.append(_line_of(current))
            current = [block]
        last_y = y
    if current:
        out.append(_line_of(current))
    return out


def _line_of(blocks: list[Block]) -> tuple[str, float]:
    text = " ".join(b.text for b in blocks)
    sizes = [s.size for b in blocks for s in b.spans if s.size]
    return text, (max(sizes) if sizes else 0.0)
