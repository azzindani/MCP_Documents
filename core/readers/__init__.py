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


def open_source(source: str, password: str = "") -> Document:
    """Open a document, from cache when the file has not changed since.

    The cache key carries mtime and size, so a document a docs-edit tool
    rewrote between two docs-read calls is re-read rather than served stale.
    That matters more here than in most caches: the two tiers share a
    filesystem by design.
    """
    key = cache.key_for(source, password)
    cached = cache.get(key)
    if cached is not None:
        return cached

    module = reader_for(source)
    doc = module.open_document(source, password)
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
