"""HTML -> core.ir.Document, via lxml.

The format everything on the web arrives as, and the one where this server's
`basis` vocabulary finally gets to say something strong. A PDF's headings are
guessed from type size (`font_size`, the weakest signal there is) and its tables
are inferred from ruling lines or column gaps. HTML *declares* both: `<h2>` is a
heading because the document says so, and `<table>` is a table because the
document says so. Every block from here carries `native`.

**What is dropped, and why it is not content.** `<script>`, `<style>`,
`<noscript>`, `<template>`, `<svg>` and HTML comments are removed before any
text is read. This is not tidying: leaving them in puts minified JavaScript into
`extract`'s output and into `find`'s snippets, where it is indistinguishable
from prose to the agent reading it, and a 300 KB page of framework bundle
becomes 75,000 tokens of nothing.

**What is kept but marked.** `<header>`, `<footer>` and `<nav>` become
`head_foot` blocks rather than being deleted. They are furniture, but they are
also where a site puts its title, its breadcrumb and its date -- so the cleaner
can drop them and a caller who wants them can still see them, which deleting
here would make impossible.

lxml's `html` parser, not `etree`: real pages are not well-formed XML, and a
strict parse fails on the unclosed `<p>` and stray `&` that every hand-written
page has. lxml recovers the way a browser does.
"""

from __future__ import annotations

from core.ir import Block, BlockKind, Document, Span
from core.readers import _flow

# Removed entirely, with their subtrees. Everything here is either a program,
# a stylesheet, or a graphic -- none of it is text a caller asked for.
DROP_TAGS = ("script", "style", "noscript", "template", "svg", "canvas", "iframe")

# Tag -> our block kind. Anything not named here contributes its text to the
# enclosing block rather than becoming one, which is what makes a page of
# nested <div><span> wrappers read as paragraphs instead of one block per div.
BLOCK_TAGS: dict[str, BlockKind] = {
    "h1": "heading",
    "h2": "heading",
    "h3": "heading",
    "h4": "heading",
    "h5": "heading",
    "h6": "heading",
    "p": "para",
    "pre": "para",
    "blockquote": "para",
    "li": "list",
    "dd": "list",
    "dt": "list",
    "figcaption": "caption",
    "caption": "caption",
    "header": "head_foot",
    "footer": "head_foot",
    "nav": "head_foot",
    "table": "table",
}

# Heading level -> a size, so the size-based machinery shared with PDF
# (`to_markdown`'s _mark_headings, outline's inference) sees what the markup
# already said. This is a MAPPING of a declared level onto the size field, not
# a measurement: the blocks also carry kind="heading" and basis="native", and
# every consumer that can read those prefers them.
HEADING_SIZES = {"h1": 26.0, "h2": 22.0, "h3": 18.0, "h4": 16.0, "h5": 14.0, "h6": 13.0}
BODY_SIZE = 12.0


def open_document(path: str, password: str = "") -> Document:
    """Parse an HTML file whole. Refuses one too large to hold in memory."""
    import lxml.html

    src = _flow.guard_size(path)
    text = _flow.decode(src)

    try:
        tree = lxml.html.document_fromstring(text)
    except Exception as exc:  # lxml raises several unrelated types on bad input
        from core.readers import ReaderError

        raise ReaderError(
            f"{src.name} could not be parsed as HTML: {exc}",
            "Check the file is HTML and not, say, a JSON or binary file with an .html name.",
        ) from exc

    for element in tree.xpath("//comment()"):
        element.getparent().remove(element)
    for tag in DROP_TAGS:
        for element in tree.iter(tag):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)

    blocks = _blocks(tree)
    meta = {
        "title": _text(tree.find(".//title")) if tree.find(".//title") is not None else "",
        "lang": tree.get("lang", ""),
        "links": len(tree.xpath("//a[@href]")),
        "images": len(tree.xpath("//img")),
        "tables": sum(1 for b in blocks if b.kind == "table"),
        "headings": sum(1 for b in blocks if b.kind == "heading"),
    }
    for element in tree.xpath("//meta[@name='description']"):
        meta["description"] = (element.get("content") or "").strip()
        break

    return _flow.build(str(src), "html", blocks, meta)


def _text(element) -> str:
    """All text under an element, whitespace collapsed to single spaces.

    `text_content()` returns the source's own whitespace, which in real HTML is
    the indentation of the markup -- a paragraph broken across six source lines
    arrives with five runs of newlines and tabs inside it.
    """
    return " ".join(element.text_content().split())


def _blocks(tree) -> list[Block]:
    """Walk the tree, emitting one block per structural element.

    Depth-first in document order, which for HTML *is* reading order -- unlike
    a PDF, where reading order has to be reconstructed from coordinates. When a
    block-level element is emitted its subtree is NOT descended into, so a `<p>`
    inside a `<li>` does not produce two overlapping blocks of the same words.

    Recursive descent rather than `tree.iter()` plus a set of visited ids.
    lxml builds a throwaway Python proxy each time a node is reached, and
    CPython reuses an id the moment a proxy is collected -- so a set of ids
    would match a node that had never been visited. The symptom was two of the
    three headings on a page silently missing, and, because collection timing
    varies, the SAME file paginating into seven pages on one read and eight on
    the next. A reader whose answer changes between two identical calls is the
    worst failure this repo has: nothing is wrong on screen, and every count
    downstream disagrees with every other.
    """
    blocks: list[Block] = []

    def descend(element) -> None:
        for child in element:
            tag = child.tag
            if not isinstance(tag, str):
                continue  # a comment or processing instruction
            kind = BLOCK_TAGS.get(tag.lower())
            if kind == "table":
                blocks.append(_table_block(child))
                continue
            if kind is not None:
                text = _text(child)
                if text:
                    blocks.append(_flow.block(text, kind, "native", HEADING_SIZES.get(tag.lower(), BODY_SIZE)))
                    continue
                # An empty block element still may WRAP content -- an <li> whose
                # text is entirely inside a nested <p>, say. Fall through and
                # descend rather than dropping the subtree.
            descend(child)

    descend(tree)

    if not blocks:
        # A page whose whole body is unwrapped text -- common in generated and
        # hand-written HTML -- has no block element to hang anything on. Its
        # text is still content, so fall back to the body rather than reporting
        # an empty document.
        body = tree.find("body")
        text = _text(body if body is not None else tree)
        if text:
            blocks.append(_flow.block(text, "para", "native", BODY_SIZE))
    return blocks


def _table_block(element) -> Block:
    """A `<table>` as one block, carrying its own rows.

    The block's own text is the table flattened to tab-separated rows, so tools
    that read text (find, extract, to_markdown) see the cell contents rather
    than a hole in the page, and the structured rows ride on `Block.rows` for
    `page_tables` to read back.
    """
    rows: list[list[str]] = []
    for row in element.iter("tr"):
        cells = [_text(cell) for cell in row.iter("td", "th")]
        if cells:
            rows.append(cells)

    flat = "\n".join("\t".join(cell for cell in row) for row in rows)
    return Block(kind="table", spans=[Span(text=flat, size=BODY_SIZE)], bbox=None, basis="native", rows=rows)


def page_tables(doc: Document, numbers: list[int]) -> list[dict]:
    """Tables on these pages. `native`, because the markup said so.

    No confidence heuristic and no ruling-line detection: a `<table>` is a
    table. The only real ambiguity in HTML tables is the layout table -- a
    `<table>` used for positioning rather than data -- and a single-column or
    single-row one is reported as it is rather than silently dropped, because
    guessing intent from shape is how a real data table with one column
    disappears.
    """
    found: list[dict] = []
    for number in numbers:
        page = doc.pages.get(number)
        if page is None:
            continue
        for block in page.blocks:
            if block.kind != "table" or not block.rows:
                continue
            rows = block.rows
            found.append(
                {
                    "page": number,
                    "rows": rows,
                    "row_count": len(rows),
                    "column_count": max(len(r) for r in rows),
                    "basis": "native",
                    "confidence": 1.0,
                }
            )
    return found


def bookmarks(doc: Document) -> list[dict]:
    """An outline from the heading elements, which HTML really does declare.

    `native`, not `font_size`. The PDF path infers headings from type size
    because that is all a PDF offers; here the document states the level, and
    reporting the same low confidence for both would throw away the difference.
    """
    out: list[dict] = []
    sizes = {size: tag for tag, size in HEADING_SIZES.items()}
    for number in sorted(doc.pages):
        for block in doc.pages[number].blocks:
            if block.kind != "heading":
                continue
            size = block.spans[0].size if block.spans else BODY_SIZE
            tag = sizes.get(size, "h6")
            out.append(
                {
                    "level": int(tag[1]),
                    "title": block.text.strip(),
                    "page": number,
                    "basis": "native",
                }
            )
    return out


close_document = _flow.close_document
load_page = _flow.load_page
load_page_words = _flow.load_page


def probe_extras(doc: Document) -> dict:
    """Format-specific facts probe() adds for HTML."""
    return {key: doc.meta[key] for key in ("title", "lang", "links", "images") if key in doc.meta}
