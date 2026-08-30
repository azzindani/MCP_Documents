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


# Separates an archive from the member inside it: `filing.zip::instance.xbrl`.
# Two colons rather than one because a Windows path starts `C:\` and a URL
# contains `https://`, and a single colon would make both ambiguous.
MEMBER_SEPARATOR = "::"

# Only these are containers whose members can be named. `.docx`, `.xlsx`,
# `.pptx` and `.epub` are zips too and are deliberately absent: they have
# readers of their own, and letting a caller reach inside one would expose
# `word/document.xml` as though reading it were a supported thing to do.
ARCHIVE_SUFFIXES = {".zip"}


def resolve_source(raw: str) -> Path:
    """A readable input path. Fetches a URL when that is enabled.

    Also resolves an archive member: `filing.zip::instance.xbrl` extracts that
    member and returns ITS path, so every tool above reads it as whatever
    format it is, with no per-tool code and no new argument on thirteen tools.

    Here, rather than in the read tier's own resolver, because both tiers call
    this one. Putting URL fetching in the read tier alone is exactly how
    `convert(source=<url>)` worked while `probe(source=<url>)` did not, and the
    fix for that defect is the reason this choke point exists to hold this.
    """
    if not raw or not raw.strip():
        raise PathError("No source given.", "Pass a file path, or a URL if MCP_FETCH_URLS=1 is set.")
    archive, separator, member = raw.strip().partition(MEMBER_SEPARATOR)
    # The separator counts ONLY when what precedes it is an archive. Not merely
    # tidy: `http://[::1]/report.pdf` contains a bare `::`, and splitting on it
    # would turn an SSRF probe the guard is meant to refuse into a nonsense
    # member lookup that never reaches the guard at all.
    if separator and Path(archive).suffix.lower() in ARCHIVE_SUFFIXES:
        return _resolve_member(archive, member)
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


def _resolve_member(archive: str, member: str) -> Path:
    """Extract one member of an archive and return the path it was written to.

    Into the inbox, which is already where this server puts material it pulled
    in from somewhere else (a fetched URL lands there too), so an extracted
    member inherits that directory's lifecycle instead of inventing a second
    one. The name carries the archive's stem, its mtime and its size, so a
    re-packed archive re-extracts rather than serving a stale member -- the
    same reasoning as the reader cache key, which this then keys off.

    The member keeps its own suffix, which is the whole trick: `reader_for`
    dispatches on the returned path, so `filing.zip::instance.xbrl` is read by
    the XBRL reader with no archive-specific code anywhere above this line.
    """
    import zipfile

    from shared.exchange import get_inbox_dir

    if not member.strip():
        raise PathError(
            f"No member named in {archive + MEMBER_SEPARATOR!r}.",
            f"Name one after '{MEMBER_SEPARATOR}', or probe('{archive}') to list them.",
        )

    from core import budget
    from core.readers import ReaderError
    from core.readers.archive import safe_member

    source = resolve_source(archive)
    stat = source.stat()
    ceiling = budget.max_source_bytes()
    try:
        with zipfile.ZipFile(source) as zf:
            info = safe_member(zf, member.strip(), source.name)
            if info.file_size > ceiling:
                raise PathError(
                    f"{info.filename!r} is {info.file_size / 1_048_576:.1f} MB, "
                    f"over the {ceiling / 1_048_576:.0f} MB limit.",
                    "Extract it outside this server and pass its path.",
                )
            target = get_inbox_dir() / f"{source.stem}-{int(stat.st_mtime)}-{stat.st_size}-{Path(info.filename).name}"
            if not target.exists() or target.stat().st_size != info.file_size:
                with zf.open(info) as handle:
                    # Bounded by the size check above, so a lying central
                    # directory header cannot talk this into writing more than
                    # the ceiling.
                    target.write_bytes(handle.read(ceiling + 1))
            apply_default_mode(target)
    except zipfile.BadZipFile as exc:
        raise PathError(
            f"{source.name} is not a readable zip archive.",
            "The file may be truncated or encrypted. Check it opens in an archive tool.",
        ) from exc
    except ReaderError as exc:
        # `safe_member` speaks the readers' error type, and this function is
        # called from BOTH tiers. The edit tier catches PathError and not
        # ReaderError, so an unsafe member name would raise straight through
        # the tool layer there while answering politely in the read tier --
        # the same split that once let a blocked URL escape as a ValueError.
        # Translated here; core.readers.resolve turns it back on the way out.
        raise PathError(str(exc), exc.hint) from exc
    return target


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
