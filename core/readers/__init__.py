"""The format-agnostic front door. One call, any document, cached.

No tool above this line asks what format it was given. `open_source` picks a
reader by extension, the reader turns the file into `core.ir.Document`, and the
cache means the intended probe -> find -> extract sequence parses a 600-page
regulation once instead of three times.

Adding a format is a reader module plus one line in READERS -- which is the
property that keeps this at 13 tools rather than 13 tools per format.

## What a reader must provide

    open_document(path, password) -> Document      required
    close_document(doc) -> None                    required
    load_page(doc, number) -> Page                 required
    load_page_words(doc, number) -> Page           required

`load_page_words` is the page WITH geometry. A format that has no geometry --
HTML, an email body, a text file -- returns the same page as `load_page`, whose
spans carry `bbox=None`, and the tools that need coordinates check for that
rather than reading zeros as real positions.

## What a reader may provide

    ruled_pages(doc, numbers) -> set[int]          default: empty
    bookmarks(doc) -> list[dict]                   default: empty

Optional because they are questions only some formats can answer. A reader that
does not define one is not missing a feature; the question does not apply to
it, and `capability()` returns the honest empty answer rather than making every
reader write a stub that returns nothing.

## Errors

Every reader raises `ReaderError` (or a subclass) with a `hint` the tool layer
hands straight to the caller. Tools catch `ReaderError`, never a specific
format's exception -- catching `PdfError` by name is how a second reader's
failures turn into unhandled tracebacks.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from core import cache
from core.ir import Document, Page


class ReaderError(Exception):
    """A document could not be read. Carries a hint the caller can act on.

    The base of every reader's error type, so the tool layer catches one class
    rather than a growing tuple that a new reader is silently absent from.
    """

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


class UnsupportedFormat(ReaderError):
    """No reader is registered for this extension."""


def _load(name: str) -> ModuleType:
    """Import a reader on first use.

    Lazily, because the readers pull heavy third-party imports -- lxml,
    python-docx, openpyxl, python-pptx -- and a probe of a PDF should not pay
    the import cost of every format this server can read. Measured: importing
    all of them eagerly costs about a second on this box, on every call.
    """
    from importlib import import_module

    return import_module(f"core.readers.{name}")


# Extension -> reader module name. The whole registry, and the whole cost of
# adding a format.
READERS = {
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".csv": "text",
    ".log": "text",
    ".docx": "office_docx",
    ".pptx": "office_pptx",
    ".xlsx": "office_xlsx",
    ".xlsm": "office_xlsx",
    ".eml": "eml",
    ".msg": "eml",
    ".epub": "epub",
    # Tagged data rather than a rendered document: the only format here whose
    # figures are stated by the filer instead of reconstructed from layout, so
    # the only one that answers `native` about a number.
    ".xbrl": "xbrl",
    # A container, not a document. It opens as its manifest and a member is read
    # as `archive.zip::member` -- see core/readers/archive.py. The zip-based
    # document formats above are routed to their own readers by extension and
    # never reach this one.
    ".zip": "archive",
}

SUPPORTED = sorted(READERS)


def reader_for(source: str) -> ModuleType:
    suffix = Path(source).suffix.lower()
    name = READERS.get(suffix)
    if name is None:
        raise UnsupportedFormat(
            f"No reader for {suffix or 'a file with no extension'!r}.",
            f"Readers available: {', '.join(SUPPORTED)}. Use convert(to='pdf') first for anything else.",
        )
    return _load(name)


def capability(doc: Document, name: str):
    """A reader's optional function, or None if that question does not apply.

    Returning None rather than raising lets the caller say "this format has no
    bookmarks" instead of "bookmarks failed", which are different answers and
    only one of them is true.
    """
    return getattr(reader_for(doc.source), name, None)


def resolve(source: str) -> str:
    """The local path behind a `source`, downloading a URL where that is on.

    This is the choke point that makes "everything is fetchable" true for the
    docs-read tier. It used to be true only for docs-edit, which resolves paths
    itself -- so `convert(source=<url>)` worked and `probe(source=<url>)` did
    not, while every reader's own not-found error ended "or pass a URL if
    MCP_FETCH_URLS=1 is set". The hint named a capability the tier did not
    have, which is worse than no hint: a caller who followed it got the same
    error again.

    A choke point rather than a line in each of the seven read tools, for the
    same reason sanitize_responses and measure_responses are applied to the
    whole server: the eighth tool cannot forget it. It is also what puts the
    SSRF guard in shared/exchange.py in front of the read tier at all -- an
    authenticated caller must not be able to turn this server into a probe of
    the network it is deployed on.
    """
    from core.paths import PathError, resolve_source

    try:
        return str(resolve_source(source))
    except PathError as exc:
        # Translated, because every tool above catches ReaderError and none
        # catches PathError -- an untranslated one would leave the read tier
        # raising through the tool layer instead of answering.
        raise ReaderError(str(exc), exc.hint) from exc


def open_source(source: str, password: str = "") -> Document:
    """Open a document, from cache when the file has not changed since.

    The cache key carries mtime and size, so a document a docs-edit tool
    rewrote between two docs-read calls is re-read rather than served stale.
    That matters more here than in most caches: the two tiers share a
    filesystem by design.
    """
    path = resolve(source)
    key = cache.key_for(path, password)
    cached = cache.get(key)
    if cached is not None:
        return cached

    module = reader_for(path)
    doc = module.open_document(path, password)
    cache.put(key, doc, module.close_document)
    return doc


def close_source(doc: Document) -> None:
    reader_for(doc.source).close_document(doc)


def load_page(doc: Document, number: int) -> Page:
    return reader_for(doc.source).load_page(doc, number)


def load_page_words(doc: Document, number: int) -> Page:
    return reader_for(doc.source).load_page_words(doc, number)


def ruled_pages(doc: Document, numbers: list[int]) -> set[int]:
    """Which of these pages carry ruling lines -- empty for formats with none.

    A format whose tables are real markup (HTML `<table>`, a docx table, a
    spreadsheet) has no ruling lines to find and does not answer this. That is
    not a gap: those tables are found by `native` basis instead, which is a
    stronger signal than a ruled one, and reporting "0 pages with ruling lines"
    for an HTML file would read as "no tables here".
    """
    found = capability(doc, "ruled_pages")
    return found(doc, numbers) if found else set()


def page_tables(doc: Document, numbers: list[int]) -> list[dict]:
    """Tables on these pages, however this format finds them.

    The whole point of routing this: a PDF's tables are reconstructed by
    pdfplumber from ruling lines or column gaps, an HTML file's are `<table>`
    elements, and a worksheet's are the sheet. Both tools that read tables used
    to `import pdfplumber` themselves, which meant `extract_tables` on anything
    but a PDF would have run pdfminer over a file it could not parse.
    """
    found = capability(doc, "page_tables")
    return found(doc, numbers) if found else []


def bookmarks(doc: Document) -> list[dict]:
    """The document's own outline, if the format carries one."""
    found = capability(doc, "bookmarks")
    return found(doc) if found else []
