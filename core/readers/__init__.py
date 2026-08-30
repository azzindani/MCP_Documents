"""The format-agnostic front door. One call, any document, cached.

No tool above this line asks what format it was given. `open_source` picks a
reader by extension, the reader turns the file into `core.ir.Document`, and the
cache means the intended probe -> find -> extract sequence parses a 600-page
regulation once instead of three times.

Adding a format is a reader module plus one line in READERS -- which is the
property that keeps this at 13 tools rather than 13 tools per format.
"""

from __future__ import annotations

from pathlib import Path

from core import cache
from core.ir import Document, Page
from core.readers import pdf


class UnsupportedFormat(Exception):
    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


READERS = {
    ".pdf": pdf,
}


def reader_for(source: str):
    suffix = Path(source).suffix.lower()
    module = READERS.get(suffix)
    if module is None:
        supported = ", ".join(sorted(READERS))
        raise UnsupportedFormat(
            f"No reader for {suffix or 'a file with no extension'!r}.",
            f"Readers available: {supported}. Use convert(to='pdf') first.",
        )
    return module


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


def load_page(doc: Document, number: int) -> Page:
    return reader_for(doc.source).load_page(doc, number)


def load_page_words(doc: Document, number: int) -> Page:
    return reader_for(doc.source).load_page_words(doc, number)


def ruled_pages(doc: Document, numbers: list[int]) -> set[int]:
    return reader_for(doc.source).ruled_pages(doc, numbers)
