"""An in-process LRU of open documents. The one place statelessness bends.

Everything else here is just-in-time: no index build, no background job, no
vector store, nothing on disk. That is deliberate and it is a requirement.

But the intended path is `probe` -> `find` -> `extract`, and on a 602-page
regulation each of those would otherwise re-parse the whole file: measured, a
probe of the 843-page CFR volume takes 4.6 seconds, so three calls is fifteen
seconds of doing the same work three times. This cache makes the second and
third calls free.

It persists nothing, adds no tool, and is invisible to the caller. The key
includes mtime and size, so a document edited between two calls is re-read
rather than served stale -- which matters here more than in most caches,
because `docs-edit` tools write files that `docs-read` tools then read.

**It must not grow a build step.** A cache is invisible; an index is a
lifecycle, with staleness, invalidation and a warm-up its callers must know
about. The moment this has one, it is a different product.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from core.ir import Document

# Documents held open at once. Each holds a PDFium handle and whatever pages
# have been read, so this is a memory budget as much as a speed one: eight
# large documents with every page loaded is already hundreds of megabytes
# against a 1 GiB container.
MAX_DOCUMENTS = int(os.environ.get("DOCS_CACHE_SIZE", "8"))

_lock = threading.Lock()
_entries: OrderedDict[tuple, Document] = OrderedDict()
_close: dict[int, Callable[[Document], None]] = {}


def key_for(path: str, password: str = "") -> tuple:
    """Identity of a document AS IT IS NOW.

    mtime_ns and size are in the key rather than checked separately: a stat
    that happens before the lookup is a race, and a document that changed
    between two calls should miss rather than be validated.

    A file that has vanished still produces a key, so the caller gets the
    reader's own "no file at ..." error rather than a cache-layer one.
    """
    try:
        stat = Path(path).stat()
        return (str(Path(path).resolve()), stat.st_mtime_ns, stat.st_size, bool(password))
    except OSError:
        return (str(path), None, None, bool(password))


def get(key: tuple) -> Document | None:
    with _lock:
        doc = _entries.get(key)
        if doc is not None:
            _entries.move_to_end(key)
        return doc


def put(key: tuple, doc: Document, closer: Callable[[Document], None]) -> None:
    """Cache a document, evicting and CLOSING the least recently used.

    The closer is stored per entry rather than imported, because core/ must not
    know which reader produced a document -- that is the whole point of the IR.
    """
    with _lock:
        _entries[key] = doc
        _close[id(doc)] = closer
        _entries.move_to_end(key)
        while len(_entries) > MAX_DOCUMENTS:
            _, evicted = _entries.popitem(last=False)
            closer_fn = _close.pop(id(evicted), None)
            if closer_fn is not None:
                closer_fn(evicted)


def clear() -> None:
    """Close and drop everything. For tests, and for a long-lived process that
    wants its file handles back."""
    with _lock:
        while _entries:
            _, doc = _entries.popitem()
            closer = _close.pop(id(doc), None)
            if closer is not None:
                closer(doc)
        _close.clear()


def stats() -> dict:
    with _lock:
        return {"documents": len(_entries), "capacity": MAX_DOCUMENTS}
