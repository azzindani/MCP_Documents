"""Turning a caller's `source` or `out` string into a real path.

Composed from `shared/exchange.py`'s primitives rather than copied from a
sibling's `file_utils.py`: that file carries workspace-alias resolution
specific to the data server, and taking it wholesale would import a vocabulary
this repo does not have. What is shared here is the URL-fetch and output-dir
behaviour, which is genuinely fleet-wide.

This is what makes "everything is fetchable" work with no per-tool code: with
MCP_FETCH_URLS=1, every `source` argument in this server accepts an http(s)
link, downloaded into the inbox and handed on as a local path. Off by default,
so a local stdio install reaches no network at all.
"""

from __future__ import annotations

from pathlib import Path

from shared.exchange import (
    apply_default_mode,
    fetch_url,
    get_output_dir,
    is_url,
    url_fetch_enabled,
)


class PathError(ValueError):
    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


def resolve_source(raw: str) -> Path:
    """A readable input path. Fetches a URL when that is enabled."""
    if not raw or not raw.strip():
        raise PathError("No source given.", "Pass a file path, or a URL if MCP_FETCH_URLS=1 is set.")
    if is_url(raw):
        if not url_fetch_enabled():
            raise PathError(
                f"{raw} is a URL and URL fetching is off on this server.",
                "Set MCP_FETCH_URLS=1 to allow it, or download the file and pass its path.",
            )
        try:
            return fetch_url(raw)
        except ValueError as exc:
            # fetch_url raises ValueError for every download failure -- the
            # SSRF guard, the size cap, a timeout, an HTTP error -- and no tool
            # in either tier catches ValueError. They catch PathError. So a URL
            # pointing at 169.254.169.254 did not produce the refusal the guard
            # was written to give; it raised straight through the tool layer,
            # out of a server whose contract is that every failure is a dict
            # with an error and a hint. It went unnoticed because the read tier
            # never reached this branch at all (see core/readers.resolve) and
            # the edit tier is rarely handed a URL.
            raise PathError(str(exc), _fetch_hint(str(exc))) from exc
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise PathError(f"No file at {raw!r}.", "Check the path, or pass a URL if MCP_FETCH_URLS=1 is set.")
    if path.is_dir():
        raise PathError(f"{raw!r} is a directory, not a document.", "Pass the path of a single file.")
    return path


def _fetch_hint(message: str) -> str:
    """The recovery for THIS download failure, not one hint for all of them.

    A single catch-all hint is wrong for every failure it was not written for,
    which is this fleet's commonest defect shape. The three cases want three
    different things from the caller, and only one of them is "try again".
    """
    lowered = message.lower()
    if "non-public address" in lowered:
        return (
            "This server refuses to fetch loopback, private and cloud-metadata addresses. "
            "Pass a publicly reachable URL, or download the file and pass its local path."
        )
    if "larger than" in lowered:
        return "Raise MCP_MAX_FETCH_MB on the server, or download the file and pass its local path."
    return "Check the URL opens in a browser and needs no login, or download the file and pass its local path."


def require_pdf(path: Path, op: str) -> None:
    """Refuse a non-PDF to a tool that only works on PDFs, by name.

    The docs-read tier is format-agnostic; most of docs-edit is not, and cannot
    be. Rotating a page, embedding an OCR layer and setting an owner password
    are operations on the PDF file format itself, not on "a document" -- there
    is no docx equivalent of linearisation.

    Checked here rather than left to the library, because the library's failure
    is unhelpful and sometimes silent: pikepdf on an HTML file says "not a PDF"
    with no suggestion, and the OCR path opened the file through the reader
    layer first, where an HTML file opens FINE and then reports every page as
    already having a text layer -- a confident, wrong, successful answer.
    """
    if path.suffix.lower() != ".pdf":
        raise PathError(
            f"{op}() works on PDFs, and {path.name} is {path.suffix or 'a file with no extension'}.",
            f"Run convert(source='{path.name}', to='pdf') first, then {op}() on the result. "
            "The docs-read tools read this format directly.",
        )


def resolve_out(raw: str, source: Path | None = None, suffix: str = ".pdf") -> Path:
    """A writable output path, defaulting into MCP_OUTPUT_DIR.

    An empty `out` derives a name from the source rather than failing: a caller
    that wants "the compressed version of this" should not have to invent a
    filename, and inventing one badly is how two tools end up overwriting each
    other's work.

    MCP_OUTPUT_DIR outranks the source's own directory, because a remote
    deployment sets it precisely so generated files land somewhere the caller
    can reach -- which the input's directory is not guaranteed to be.
    """
    if raw and raw.strip():
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = get_output_dir() / path
    elif source is not None:
        path = get_output_dir() / f"{source.stem}_out{suffix}"
    else:
        raise PathError("No output path given.", "Pass out='name.pdf'.")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def finish(path: Path) -> None:
    """Make a written file readable by anything else sharing the directory.

    mkstemp creates 0600 and an atomic rename preserves it, so every generated
    file in a sibling repo was unreadable to the very services meant to consume
    it. Found by running it, not by a test.
    """
    apply_default_mode(path)
