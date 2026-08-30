"""XBRL instance -> core.ir.Document, via the stdlib XML parser.

The only format this server reads whose numbers arrive already tagged.

Everything else here reconstructs. A PDF is glyphs at coordinates, so a figure
in a financial statement is recovered from ruling lines or column gaps and
carries `ruled` or `whitespace` confidence; a table cell can be truncated by a
bounding box, and a number can be glued to the next column's label. An XBRL
instance states `<Assets contextRef="CurrentYearInstant">1640830566000000`. The
issuer wrote that number down in a machine-readable field. There is nothing to
infer and nothing to get wrong, so every fact here is `native`.

That makes this reader the strongest answer this server can give about a listed
company's figures, and it is worth saying plainly in the response rather than
leaving a caller to assume a PDF and an XBRL are equally reliable. They are not.

**Facts, contexts and units are three different things and the response keeps
them apart.** A bare fact value is close to useless: `634224104000000` means
nothing until you know it is savings deposits, held at 31 March 2026, in
rupiah, by third parties rather than related ones. The context carries the
period and the dimensional members, the unit carries the currency, and a row
that dropped either would be a number without a meaning -- which is precisely
the failure this repo exists to avoid.

**Numbers are not converted.** An instance states full units (rupiah), while
the PDF it accompanies prints millions. Rescaling here would silently produce a
figure that matches neither document, so values are reported exactly as filed
and `decimals` is carried alongside so a caller can see the filer's own claim
about precision.
"""

from __future__ import annotations

from core.ir import Block, Document, Span
from core.readers import _flow

XBRLI = "{http://www.xbrl.org/2003/instance}"

# A fact whose local name ends with this is narrative, not a number: a
# paragraph of accounting policy the filer tagged as a block of text. They are
# real prose and often the only explanation of why a figure moved, so they are
# read as text rather than dropped -- one BBCA filing carries 40 of them.
TEXT_BLOCK_SUFFIX = "TextBlock"

# Rows in one table block before it is split. _flow._split_oversized would
# split anyway on character count; this keeps the split on a row boundary so a
# fact is never cut in half across two pages.
FACTS_PER_BLOCK = 120


def _error(message: str, hint: str):
    """A ReaderError, imported late to keep this module free of a cycle.

    `core.readers` imports readers lazily and readers import from it, so the
    import lives in the function rather than at module scope -- the same shape
    every other reader here uses.
    """
    from core.readers import ReaderError

    return ReaderError(message, hint)


def open_document(path: str, password: str = "") -> Document:
    """Read an XBRL instance: every fact, with its context and unit.

    The stdlib parser, not lxml. Every other XML-ish reader here uses lxml
    because it recovers from malformed markup the way a browser does, and real
    HTML needs that. An XBRL instance does not: it is machine-generated and
    well-formed by definition, so the recovery buys nothing and the dependency
    costs a stub problem on three CI runners.
    """
    import xml.etree.ElementTree as ET

    src = _flow.guard_size(path)
    try:
        tree = ET.parse(str(src))
    except ET.ParseError as exc:
        raise _error(
            f"{src.name} is not readable XML: {exc}",
            "The file may be truncated. An XBRL instance is XML; check it opens in a browser.",
        ) from exc

    root = tree.getroot()
    contexts = _contexts(root)
    units = _units(root)

    numeric: list[list[str]] = []
    narrative: list[tuple[str, str]] = []
    for element in root.iter():
        ref = element.get("contextRef")
        value = (element.text or "").strip()
        if not ref or not value:
            continue
        name = _local(element.tag)
        if name.endswith(TEXT_BLOCK_SUFFIX):
            narrative.append((name, value))
            continue
        numeric.append(
            [
                name,
                contexts.get(ref, ref),
                units.get(element.get("unitRef") or "", ""),
                value,
                element.get("decimals") or "",
            ]
        )

    if not numeric and not narrative:
        raise _error(
            f"{src.name} carries no XBRL facts.",
            "This is XML but not an XBRL instance. An instance has elements with a contextRef.",
        )

    blocks = _blocks(numeric, narrative)
    doc = _flow.build(
        str(src),
        "xbrl",
        blocks,
        meta={
            "facts": len(numeric) + len(narrative),
            # Counted by whether the fact carries a UNIT, not by how many rows
            # the table has. Only numeric facts have a unitRef in XBRL -- an
            # `EntityName` or a filing date is a tagged fact with a context and
            # no unit -- so counting rows here would report a company's name as
            # one of its numbers.
            "numeric_facts": sum(1 for row in numeric if row[2]),
            "other_facts": sum(1 for row in numeric if not row[2]),
            "text_blocks": len(narrative),
            "contexts": len(contexts),
            "units": sorted({u for u in units.values() if u}),
            "entity": _entity(root),
        },
        basis="native",
    )
    return doc


def _blocks(numeric: list[list[str]], narrative: list[tuple[str, str]]) -> list[Block]:
    blocks: list[Block] = []
    if numeric:
        blocks.append(_flow.block("Facts", kind="heading", basis="native", size=16.0))
        header = ["fact", "context", "unit", "value", "decimals"]
        for start in range(0, len(numeric), FACTS_PER_BLOCK):
            chunk = numeric[start : start + FACTS_PER_BLOCK]
            rows = [header, *chunk]
            item = Block(
                kind="table",
                spans=[Span(text="\n".join("\t".join(r) for r in rows))],
                basis="native",
            )
            item.rows = rows
            blocks.append(item)

    for name, text in narrative:
        blocks.append(_flow.block(_humanise(name), kind="heading", basis="native", size=14.0))
        for paragraph in text.split("\n"):
            if paragraph.strip():
                blocks.append(_flow.block(paragraph.strip(), basis="native"))
    return blocks


def _humanise(name: str) -> str:
    """`DisclosureofInterestandShariaIncomeTextBlock` -> a readable heading.

    The element name is the only title these sections have -- an instance
    carries no headings of its own -- so it is split on camel case rather than
    printed raw, and the `TextBlock` suffix that every one of them shares is
    dropped because a heading repeated forty times is not a heading.
    """
    import re

    stem = name[: -len(TEXT_BLOCK_SUFFIX)] if name.endswith(TEXT_BLOCK_SUFFIX) else name
    stem = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem)
    return stem.replace("Disclosureof", "Disclosure of ").strip() or name


def _local(tag: object) -> str:
    """The local name of a namespaced tag: `{ns}Assets` -> `Assets`."""
    return str(tag).rsplit("}", 1)[-1]


def _contexts(root) -> dict[str, str]:
    """Context id -> a readable period, with any dimensional members.

    Resolved from the context ELEMENT rather than parsed out of the id string.
    Ids like `CurrentYearInstant_4622100_RupiahMember_ThirdPartiesMember` look
    parseable and are a filer convention, not a rule -- an SEC instance numbers
    them `c-3`, which carries the same meaning and none of the same text.
    """
    out: dict[str, str] = {}
    for context in root.iter(f"{XBRLI}context"):
        cid = context.get("id")
        if not cid:
            continue
        instant = context.findtext(f".//{XBRLI}instant")
        start = context.findtext(f".//{XBRLI}startDate")
        end = context.findtext(f".//{XBRLI}endDate")
        if instant:
            period = f"at {instant.strip()}"
        elif start and end:
            period = f"{start.strip()} to {end.strip()}"
        else:
            period = cid

        members = [
            (member.text or "").strip().split(":")[-1]
            for member in context.iter()
            if _local(member.tag) == "explicitMember" and member.text
        ]
        out[cid] = f"{period} [{', '.join(members)}]" if members else period
    return out


def _units(root) -> dict[str, str]:
    """Unit id -> its measure, e.g. `iso4217:IDR` -> `IDR`, `xbrli:shares`."""
    out: dict[str, str] = {}
    for unit in root.iter(f"{XBRLI}unit"):
        uid = unit.get("id")
        if not uid:
            continue
        measures = [(m.text or "").strip().split(":")[-1] for m in unit.iter() if _local(m.tag) == "measure" and m.text]
        out[uid] = "/".join(measures)
    return out


def _entity(root) -> str:
    """Who filed this, as the instance identifies them."""
    for identifier in root.iter(f"{XBRLI}identifier"):
        if identifier.text and identifier.text.strip():
            return identifier.text.strip()
    return ""


def page_tables(doc: Document, numbers: list[int]) -> list[dict]:
    """The facts on these pages, as rows. `native`, confidence 1.0.

    Same keys as the HTML reader's, deliberately. `extract_tables` filters on
    `confidence` for every format, so a reader that omitted it because its
    tables are certain would raise KeyError on the filter instead of passing
    it -- a "more honest" shape that breaks the tool. Consistency across
    formats is what makes the router's one-contract promise true; `basis` is
    where the difference between declared and inferred is already recorded.
    """
    found: list[dict] = []
    for number in numbers:
        page = doc.pages.get(number)
        if page is None:
            continue
        for block in page.blocks:
            if block.kind != "table" or not block.rows:
                continue
            found.append(
                {
                    "page": number,
                    "rows": block.rows,
                    "row_count": len(block.rows),
                    "column_count": max(len(r) for r in block.rows),
                    "basis": "native",
                    "confidence": 1.0,
                }
            )
    return found


def bookmarks(doc: Document) -> list[dict]:
    """The narrative sections, which are the only headings an instance has."""
    out: list[dict] = []
    for number in sorted(doc.pages):
        for block in doc.pages[number].blocks:
            if block.kind == "heading" and block.text.strip():
                out.append({"level": 1, "title": block.text.strip(), "page": number, "basis": "native"})
    return out


def probe_extras(doc: Document) -> dict:
    """What an XBRL instance knows about itself and no other format does."""
    meta = doc.meta
    return {
        "entity": meta.get("entity", ""),
        "facts": meta.get("facts", 0),
        "numeric_facts": meta.get("numeric_facts", 0),
        "other_facts": meta.get("other_facts", 0),
        "text_blocks": meta.get("text_blocks", 0),
        "contexts": meta.get("contexts", 0),
        "units": meta.get("units", []),
        # The reason to prefer this file over the PDF beside it, said once,
        # where a caller deciding which to read will see it.
        "note": "Facts are tagged by the filer, not inferred from layout. Values are as filed, not rescaled.",
    }


def load_page(doc: Document, number: int):
    return _flow.load_page(doc, number)


def load_page_words(doc: Document, number: int):
    """No geometry: an instance is tagged data, not a rendered page.

    Same page as load_page, whose spans carry bbox=None, so the tools that need
    coordinates see there are none rather than reading zeros as real positions.
    """
    return _flow.load_page(doc, number)


def close_document(doc: Document) -> None:
    _flow.close_document(doc)


__all__ = [
    "bookmarks",
    "close_document",
    "load_page",
    "load_page_words",
    "open_document",
    "page_tables",
    "probe_extras",
]
