"""ZIP -> core.ir.Document, and the `archive.zip::member` selector.

**A zip is a container, not a document, and this reader does not pretend
otherwise.** The temptation is to map members onto pages so that every existing
tool appears to work on an archive. It would be a lie in the response's own
vocabulary: `extract(pages='3-5')` would return three filenames, `page_size`
would be null for something that has no pages at all, and a caller who asked
for "the text of this document" would get a directory listing described as
text.

So an archive opens as ONE page whose content is its manifest -- which is the
one thing a zip really does contain -- and a member is read by naming it:

    probe("filing.zip")                    what is in here
    probe("filing.zip::instance.xbrl")     the member, read as an XBRL

The `::` selector is resolved in `core.paths.resolve_source`, the choke point
BOTH tiers share. Putting it in the read tier alone is how `source=<url>`
worked in docs-edit and not in docs-read for a whole phase, and the fix for
that defect is the reason there is a shared choke point to put this in.

**.docx, .xlsx, .pptx and .epub are zips too**, and they are NOT routed here.
The registry maps them to their own readers by extension, which is what makes
`probe("report.docx")` read a document rather than list `word/document.xml`.
Only a bare `.zip` reaches this module.

## Why the bomb guard is a size and not a ratio

The obvious guard is a compression ratio, and measured against the corpus it is
the wrong one. Real XBRL archives compress **6.9x to 31x** -- XML is repetitive
and packs extremely well -- so the intuitive "anything over 10x is a bomb"
rejects every genuine filing in `/root/Evals/investment`. A zip bomb is not
distinguished by being compressible; it is distinguished by expanding to more
bytes than anyone could want. The absolute total is the real guard, and the
ratio is only a second tripwire set far above anything real.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from core import budget
from core.ir import Block, Document, Span
from core.readers import _flow

# Members in an archive before it is refused. Real filings carry 2 to 41; a
# ceiling this far above that is aimed at an archive of ten thousand tiny files,
# whose manifest alone would blow the response budget.
MAX_MEMBERS = 2000


# The real guard. An archive is refused when its members would together exceed
# what a single source file is allowed to be, because extracting one member is
# how this server would spend that memory.
def max_total_bytes() -> int:
    return budget.max_source_bytes()


# The second tripwire, and deliberately far from anything real. Measured across
# the corpus the worst genuine ratio is 31x (an IDX taxonomy archive); the
# classic 42.zip is in the billions. 200 leaves every real document alone and
# still stops the thing this is for.
MAX_RATIO = 200

# Members named in a probe() response. probe answers in roughly 150 tokens for
# a 600-page regulation and that budget is the reason it is the first call in
# every workflow; an archive is not entitled to spend more. The full manifest
# is always available as rows from extract_tables().
MAX_LISTED = 50


def open_document(path: str, password: str = "") -> Document:
    """Read a .zip as its manifest: what is inside and how to open each part."""
    src = _flow.guard_size(path)
    archive = _open(src)
    try:
        members = _members(archive, src.name)
    finally:
        archive.close()

    rows = [["member", "format", "bytes", "readable"]]
    for entry in members:
        rows.append([entry["name"], entry["format"], f"{entry['bytes']:,}", "yes" if entry["readable"] else "no"])

    manifest = Block(kind="table", spans=[Span(text="\n".join("\t".join(r) for r in rows))], basis="native")
    manifest.rows = rows
    blocks = [
        _flow.block(f"{src.name}: {len(members)} member(s)", kind="heading", basis="native", size=16.0),
        manifest,
    ]

    return _flow.build(
        str(src),
        "zip",
        blocks,
        meta={
            "members": members,
            "member_count": len(members),
            "uncompressed_bytes": sum(m["bytes"] for m in members),
        },
        basis="native",
    )


def _open(src: Path) -> zipfile.ZipFile:
    from core.readers import ReaderError

    try:
        return zipfile.ZipFile(src)
    except zipfile.BadZipFile as exc:
        raise ReaderError(
            f"{src.name} is not a readable zip archive.",
            "The file may be truncated or encrypted. Check it opens in an archive tool.",
        ) from exc


def _members(archive: zipfile.ZipFile, name: str) -> list[dict]:
    """The archive's entries, after the guards, with what this server can read.

    Directories are dropped rather than listed: a zip records them as zero-byte
    entries ending in "/", and reporting them as members would tell a caller
    there is something to open that cannot be opened.
    """
    from core.readers import READERS, ReaderError

    entries = [info for info in archive.infolist() if not info.is_dir()]
    if len(entries) > MAX_MEMBERS:
        raise ReaderError(
            f"{name} holds {len(entries)} members, over the {MAX_MEMBERS} limit.",
            "Extract the part you need and pass its path.",
        )

    total = sum(info.file_size for info in entries)
    packed = sum(info.compress_size for info in entries)
    ceiling = max_total_bytes()
    if total > ceiling:
        raise ReaderError(
            f"{name} expands to {total / 1_048_576:.1f} MB, over the {ceiling / 1_048_576:.0f} MB limit.",
            "Extract the member you need outside this server and pass its path.",
        )
    if packed and total / packed > MAX_RATIO:
        raise ReaderError(
            f"{name} expands {total / packed:.0f}x, over the {MAX_RATIO}x limit.",
            "This ratio is characteristic of a decompression bomb rather than a document archive.",
        )

    out: list[dict] = []
    for info in entries:
        suffix = Path(info.filename).suffix.lower()
        out.append(
            {
                "name": info.filename,
                "format": suffix.lstrip(".") or "none",
                "bytes": info.file_size,
                "readable": suffix in READERS,
            }
        )
    return out


def safe_member(archive: zipfile.ZipFile, member: str, archive_name: str) -> zipfile.ZipInfo:
    """The named entry, refusing the ones that must never be extracted.

    Two refusals, and both are about writing outside the directory we chose:
    an absolute path, and any path that walks upward. Python's `extract` has
    sanitised these since 3.6, but this server does not call `extract` -- it
    reads the member and writes it under a name it picks -- so the check has to
    be here rather than assumed from the library.
    """
    from core.readers import ReaderError

    normalised = member.replace("\\", "/").strip()
    if normalised.startswith("/") or ".." in Path(normalised).parts:
        raise ReaderError(
            f"{member!r} is not a safe member name.",
            "Member paths may not be absolute or contain '..'.",
        )
    try:
        return archive.getinfo(normalised)
    except KeyError:
        available = [i.filename for i in archive.infolist() if not i.is_dir()][:12]
        raise ReaderError(
            f"{archive_name} has no member {member!r}.",
            f"Members: {', '.join(available)}. probe('{archive_name}') lists them all.",
        ) from None


def page_tables(doc: Document, numbers: list[int]) -> list[dict]:
    """The manifest, as rows. `native` -- the archive's own directory."""
    found: list[dict] = []
    for number in numbers:
        page = doc.pages.get(number)
        if page is None:
            continue
        for block in page.blocks:
            if block.kind == "table" and block.rows:
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


def probe_extras(doc: Document) -> dict:
    """What is in the archive, and how to open it.

    `open_with` is the point of the whole reader: a caller who probes an archive
    must be told the syntax for reading a member, or the listing is a dead end.
    """
    members = doc.meta.get("members", [])
    readable = [m["name"] for m in members if m["readable"]]
    name = Path(doc.source).name
    extras = {
        "member_count": doc.meta.get("member_count", 0),
        "uncompressed_bytes": doc.meta.get("uncompressed_bytes", 0),
        # Bounded, like every other list this server returns. The guard allows
        # 2,000 members and probe's whole job is to answer in ~150 tokens, so
        # listing them all here would make the tool that exists to keep a
        # document addressable the most expensive call in the server.
        "members": [{"name": m["name"], "format": m["format"], "bytes": m["bytes"]} for m in members[:MAX_LISTED]],
        "readable_members": readable[:MAX_LISTED],
    }
    if len(members) > MAX_LISTED:
        # Counts stay exact even when the list does not -- the same rule find()
        # follows, so a caller can tell "12 members, here they are" from
        # "900 members, here are the first 50".
        extras["members_truncated"] = True
        extras["note_truncated"] = (
            f"{len(members)} members, {MAX_LISTED} listed. extract_tables('{name}') returns the full manifest as rows."
        )
    if readable:
        extras["open_with"] = f"{name}::{readable[0]}"
    else:
        extras["note"] = "No member is in a format this server reads. Convert one outside and pass its path."
    return extras


def load_page(doc: Document, number: int):
    return _flow.load_page(doc, number)


def load_page_words(doc: Document, number: int):
    """No geometry: an archive has no rendered page."""
    return _flow.load_page(doc, number)


def close_document(doc: Document) -> None:
    _flow.close_document(doc)


__all__ = [
    "close_document",
    "load_page",
    "load_page_words",
    "open_document",
    "page_tables",
    "probe_extras",
    "safe_member",
]
