"""EPUB -> core.ir.Document, from the zip and the OPF, via lxml.

**One spine item is one page**, which is a real boundary: an epub's spine is
the publisher's own statement of reading order, and its items are usually
chapters. `_flow.paged`, not `_flow.build` -- nothing here is synthetic.

No third-party epub library. An epub is a zip holding a `container.xml` that
names an OPF package document, which names a manifest and a spine; every one of
those is XML this repo already parses, and the reading is fifty lines. Pulling
in a library for it would add a dependency, a licence to check and a version to
track for no capability.

**Spine order, not manifest order, and not archive order.** The manifest is an
unordered set of files and the zip's own order is whatever the packer did. Only
the spine says which chapter follows which, and getting this wrong produces a
book whose chapters are alphabetical by filename -- readable, plausible, and
wrong, which is the worst kind of answer this server can give.
"""

from __future__ import annotations

import posixpath
import zipfile

from core.ir import Document, Page
from core.readers import _flow

CONTAINER = "META-INF/container.xml"
OPF_NS = "{http://www.idpf.org/2007/opf}"
CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
DC_NS = "{http://purl.org/dc/elements/1.1/}"


def open_document(path: str, password: str = "") -> Document:
    """Read an .epub: one page per spine item, in the publisher's order."""
    from core.readers import ReaderError

    src = _flow.guard_size(path)
    try:
        archive = zipfile.ZipFile(src)
    except zipfile.BadZipFile as exc:
        raise ReaderError(
            f"{src.name} is not a readable epub: the file is not a valid zip archive.",
            "The file may be truncated. Check it opens in an e-reader.",
        ) from exc

    try:
        opf_path = _opf_path(archive)
        package = _xml(archive, opf_path)
        base = posixpath.dirname(opf_path)

        manifest = {
            item.get("id"): item.get("href")
            for item in package.iter(f"{OPF_NS}item")
            if item.get("id") and item.get("href")
        }
        spine = [
            manifest[ref.get("idref")] for ref in package.iter(f"{OPF_NS}itemref") if manifest.get(ref.get("idref"))
        ]
        if not spine:
            raise ReaderError(
                f"{src.name} has no reading order: its spine is empty.",
                "The file may be a malformed epub. Try convert(to='pdf') on it first.",
            )

        pages: list[Page] = []
        for index, href in enumerate(spine, start=1):
            name = posixpath.normpath(posixpath.join(base, href)) if base else href
            blocks = _chapter_blocks(archive, name)
            page = Page(number=index, basis="native" if blocks else "empty")
            for order, block in enumerate(blocks):
                block.order = order
            page.blocks = blocks
            pages.append(page)

        meta = {
            "title": _dc(package, "title"),
            "author": _dc(package, "creator"),
            "language": _dc(package, "language"),
            "spine_items": len(spine),
        }
    finally:
        archive.close()

    return _flow.paged(str(src), "epub", pages, meta)


def _opf_path(archive: zipfile.ZipFile) -> str:
    """The package document's path, from META-INF/container.xml.

    Read rather than guessed. `content.opf` at the root is the common layout
    and not the required one; container.xml exists precisely to say where the
    package document is, and a reader that assumes fails on every epub built by
    a tool that nests it.
    """
    from core.readers import ReaderError

    try:
        container = _xml(archive, CONTAINER)
    except KeyError as exc:
        raise ReaderError(
            "This file has no META-INF/container.xml, so it is not an epub.",
            "Check the file is an epub and not a renamed zip.",
        ) from exc

    for rootfile in container.iter(f"{CONTAINER_NS}rootfile"):
        full = rootfile.get("full-path")
        if full:
            return full
    raise ReaderError(
        "This epub's container.xml names no package document.",
        "The file is malformed. Try opening it in an e-reader to confirm.",
    )


def _xml(archive: zipfile.ZipFile, name: str):
    import lxml.etree as etree

    # resolve_entities=False: an epub is an untrusted file from the internet,
    # and an XML parser that resolves external entities will happily read
    # /etc/passwd into a chapter for it. lxml disables network access by
    # default; this closes the local half.
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    return etree.fromstring(archive.read(name), parser)


def _dc(package, name: str) -> str:
    element = package.find(f".//{DC_NS}{name}")
    return (element.text or "").strip() if element is not None and element.text else ""


def _chapter_blocks(archive: zipfile.ZipFile, name: str) -> list:
    """One spine item's XHTML, through this package's own HTML reader.

    Reusing `html._blocks` rather than writing a second walker means an epub
    chapter and a web page produce the same block kinds from the same markup --
    and a fix to heading or table handling lands in both.
    """
    import lxml.html

    from core.readers.html import DROP_TAGS, _blocks

    try:
        raw = archive.read(name)
    except KeyError:
        # A spine entry pointing at a file the archive does not contain. The
        # book is still readable; this chapter is empty and says so, rather
        # than taking the whole document down.
        return []
    try:
        tree = lxml.html.document_fromstring(raw)
    except Exception:
        return []
    for tag in DROP_TAGS:
        for element in tree.iter(tag):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
    return _blocks(tree)


def page_tables(doc: Document, numbers: list[int]) -> list[dict]:
    from core.readers.html import page_tables as _shared

    return _shared(doc, numbers)


def bookmarks(doc: Document) -> list[dict]:
    from core.readers.html import bookmarks as _shared

    return _shared(doc)


def probe_extras(doc: Document) -> dict:
    return {key: doc.meta[key] for key in ("title", "author", "language", "spine_items") if key in doc.meta}


close_document = _flow.close_document
load_page = _flow.load_page
load_page_words = _flow.load_page
