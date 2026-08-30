"""protect() and redact().

`redact` is the one tool in this repo whose failure hurts a person rather than
an agent. Covering text with a black rectangle leaves it fully extractable
underneath -- that is how redaction failures reach the news -- so this removes
the glyphs from the content stream and then **re-extracts the region to confirm
nothing matches**. `success: true` with `verified: false` is impossible by
construction, and if verification cannot run at all the call fails.

`protect` deliberately requires the password to decrypt. This repo does not
attempt to recover a password it was not given, whatever "unlock" means on
comparable sites. Removing owner-permission flags from a document the caller
can already open is supported, and is what "unlock" usually means in practice.
"""

from __future__ import annotations

import re

import pikepdf

from core.formatter import fail, ok
from core.paths import PathError, finish, resolve_out, resolve_source
from core.selection import SelectionError, format_pages, parse_pages
from shared.progress import info, warn
from shared.progress import ok as ok_step

ACTIONS = ("encrypt", "decrypt", "permissions")


def protect(source: str, action: str, password: str = "", out: str = "") -> dict:
    """Encrypt, decrypt or set permissions on a PDF you have the password for."""
    op = "protect"
    progress: list[dict] = []
    if action not in ACTIONS:
        return fail(
            op,
            f"{action!r} is not an action this tool has.",
            f"Use one of: {', '.join(ACTIONS)}.",
            progress,
        )
    if action in {"encrypt", "decrypt"} and not password:
        return fail(
            op,
            f"{action} needs a password.",
            "Pass password='...'. This tool cannot recover a password it was not given.",
            progress,
        )

    try:
        src = resolve_source(source)
        destination = resolve_out(out, src)
    except PathError as exc:
        return fail(op, str(exc), exc.hint, progress)

    try:
        # Open WITHOUT the password first, and only supply it if the file
        # actually needs one. pikepdf warns "A password was provided, but no
        # password was needed to open this PDF" otherwise -- and this repo's own
        # contract forbids warning text reaching a response, so emitting one at
        # all is a habit worth not having.
        try:
            handle = pikepdf.open(src)
        except pikepdf.PasswordError:
            handle = pikepdf.open(src, password=password or "")
    except pikepdf.PasswordError:
        return fail(
            op,
            f"{src.name} is encrypted and that password did not open it.",
            "Pass the correct password. There is no recovery path here by design.",
            progress,
        )
    except pikepdf.PdfError as exc:
        return fail(op, f"Could not open {src.name}: {exc}", "Try optimize(action='repair') first.", progress)

    try:
        if action == "encrypt":
            handle.save(destination, encryption=pikepdf.Encryption(owner=password, user=password, R=6))
            progress.append(ok_step("encrypted with AES-256 (R=6)"))
        elif action == "decrypt":
            handle.save(destination)  # saving without encryption= drops it
            progress.append(ok_step("saved without encryption"))
        else:
            # Owner permissions only: the document stays encrypted for anyone
            # without the password, and the restriction flags that only ever
            # constrained a cooperating reader are cleared.
            handle.save(
                destination,
                encryption=pikepdf.Encryption(owner=password, user="", allow=pikepdf.Permissions(), R=6)
                if password
                else False,
            )
            progress.append(ok_step("permission flags cleared"))
        finish(destination)
    except (pikepdf.PdfError, OSError) as exc:
        return fail(
            op, f"Could not write {destination.name}: {exc}", "Check the output directory is writable.", progress
        )
    finally:
        handle.close()

    # Read the result back rather than reporting what we asked for. A tool that
    # says "encrypted" because it called an encrypt function has verified
    # nothing -- this is the same class as a redaction that reports success
    # without checking, and the cost of checking is one open.
    encrypted_now = _is_encrypted(destination, password)
    result = {
        "out": str(destination),
        "action": action,
        "encrypted": encrypted_now,
        "bytes": destination.stat().st_size,
    }
    if action == "encrypt" and not encrypted_now:
        return fail(
            op,
            "The output was written but is not encrypted.",
            "This is a bug in the server, not in your call. Do not distribute the file.",
            progress,
            **result,
        )
    return ok(op, result, progress)


def _is_encrypted(path, password: str) -> bool:
    try:
        with pikepdf.open(path) as handle:
            return bool(handle.is_encrypted)
    except pikepdf.PasswordError:
        return True
    except pikepdf.PdfError:
        return False


def redact(source: str, pattern: str, pages: str = "", regex: bool = False, out: str = "") -> dict:
    """Permanently remove matching content, then verify it cannot be extracted."""
    op = "redact"
    progress: list[dict] = []
    if not pattern:
        return fail(op, "No pattern given.", "Pass the text to remove, e.g. pattern='ACCOUNT-1234'.", progress)

    try:
        src = resolve_source(source)
        destination = resolve_out(out, src)
    except PathError as exc:
        return fail(op, str(exc), exc.hint, progress)

    try:
        matcher = re.compile(pattern if regex else re.escape(pattern), re.IGNORECASE)
    except re.error as exc:
        return fail(
            op,
            f"{pattern!r} is not a valid regular expression: {exc}",
            "Pass regex=False to match it literally.",
            progress,
        )

    try:
        handle = pikepdf.open(src)
    except pikepdf.PdfError as exc:
        return fail(op, f"Could not open {src.name}: {exc}", "Try optimize(action='repair') first.", progress)

    try:
        wanted = parse_pages(pages, len(handle.pages))
    except SelectionError as exc:
        handle.close()
        return fail(op, str(exc), exc.hint, progress)

    removed = 0
    touched: list[int] = []
    try:
        for number in wanted:
            page = handle.pages[number - 1]
            count = _strip_from_page(handle, page, matcher)
            if count:
                removed += count
                touched.append(number)
        handle.save(destination)
        finish(destination)
    except (pikepdf.PdfError, OSError) as exc:
        handle.close()
        return fail(
            op, f"Could not write {destination.name}: {exc}", "Check the output directory is writable.", progress
        )
    handle.close()

    progress.append(info(f"removed {removed} text run(s) from {len(touched)} page(s)"))

    # THE VERIFICATION. Re-extract the written file and confirm the pattern is
    # gone. Without this the tool is a claim; with it, it is a check. A
    # redaction that reports success it did not verify is the failure this
    # whole design exists to prevent.
    residual = _still_extractable(destination, matcher, wanted)
    result = {
        "out": str(destination),
        "redacted": removed,
        "pages": format_pages(touched),
        "verified": not residual,
        "residual_matches": len(residual),
    }
    if residual:
        progress.append(warn("the pattern is still extractable from the output", f"pages {format_pages(residual)}"))
        return fail(
            op,
            f"Redaction could not be verified: the pattern is still extractable on page(s) {format_pages(residual)}.",
            "Do not distribute this file. The text is likely drawn with a subset font whose bytes do not "
            "match the pattern; extract(pages=...) to see how it is encoded.",
            progress,
            **result,
        )
    progress.append(ok_step("verified: the pattern is no longer extractable"))
    return ok(op, result, progress)


def _strip_from_page(pdf: pikepdf.Pdf, page: pikepdf.Page, matcher: re.Pattern) -> int:
    """Delete text-showing operators whose text matches, in the content stream.

    Operates on the content stream itself rather than drawing over the page,
    because drawing leaves the glyphs behind for any extractor to read. Tj and
    ' take a single string; TJ takes an array of strings and kerning numbers.

    This works where the text is stored as readable bytes. Where a subset font
    encodes it otherwise the operators will not match, nothing is removed, and
    the verification step below refuses the file -- which is the correct
    outcome, and the reason verification is not optional.
    """
    removed = 0
    instructions = []
    for operands, operator in pikepdf.parse_content_stream(page):
        name = str(operator)
        if name in {"Tj", "'", '"'} and operands:
            text = _as_text(operands[-1])
            if text and matcher.search(text):
                removed += 1
                continue
        elif name == "TJ" and operands:
            array = operands[0]
            # A TJ operand is a pikepdf Array of strings and kerning numbers.
            # pikepdf types it as the generic Object, which pyright does not
            # know is iterable; it is, and _as_text drops the numbers.
            joined = "".join(_as_text(item) or "" for item in array)  # type: ignore[union-attr]
            if joined and matcher.search(joined):
                removed += 1
                continue
        instructions.append((operands, operator))
    if removed:
        page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(instructions))
    return removed


def _as_text(operand) -> str | None:
    try:
        return bytes(operand).decode("latin-1")
    except TypeError, ValueError, UnicodeDecodeError:
        return None


def _still_extractable(path, matcher: re.Pattern, pages: list[int]) -> list[int]:
    """Which pages still yield the pattern when read back. The proof."""
    import pypdfium2 as pdfium

    remaining: list[int] = []
    document = pdfium.PdfDocument(path)
    try:
        for number in pages:
            text = document[number - 1].get_textpage().get_text_bounded()
            if matcher.search(text):
                remaining.append(number)
    finally:
        document.close()
    return remaining
