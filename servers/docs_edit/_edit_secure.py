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
from collections.abc import Callable
from decimal import Decimal

import pikepdf

from core import budget
from core.formatter import fail, ok
from core.paths import PathError, finish, require_pdf, resolve_out, resolve_source
from core.selection import SelectionError, format_pages, parse_pages
from shared.progress import info, warn
from shared.progress import ok as ok_step
from shared.regex_guard import Guard, PatternTimeout

ACTIONS = ("encrypt", "decrypt", "permissions")

# Every restriction off, written out. `pikepdf.Permissions()` reads like "no
# permissions object", and it is not: it is a set of defaults, one of which
# (`modify_assembly`) is False. A tool whose whole job is saying what a file
# now allows cannot take that on trust -- measure the library call, do not
# assume what it does with your value.
ALL_ALLOWED = pikepdf.Permissions(
    accessibility=True,
    extract=True,
    modify_annotation=True,
    modify_assembly=True,
    modify_form=True,
    modify_other=True,
    print_lowres=True,
    print_highres=True,
)


def protect(source: str, action: str, password: str = "", out: str = "") -> dict:
    """PDF security. action: encrypt, decrypt, permissions. Needs a password."""
    op = "protect"
    progress: list[dict] = []
    if action not in ACTIONS:
        return fail(
            op,
            f"{action!r} is not an action this tool has.",
            f"Use one of: {', '.join(ACTIONS)}.",
            progress,
        )
    # All three, not just two. Permission flags only exist inside encryption,
    # so `permissions` without a password had nothing to re-encrypt with and
    # fell through to `encryption=False` -- which SILENTLY DECRYPTED the
    # document. Handed a filing that was encrypted with printing and copying
    # forbidden, it returned success, `action: "permissions"` and the progress
    # line "permission flags cleared", and wrote a file any reader opens with
    # no password at all. The one field that told the truth was `encrypted:
    # false`, next to an action name that says nothing about encryption.
    if not password:
        return fail(
            op,
            f"{action} needs a password.",
            "Pass password='...'. Permission flags live inside a PDF's encryption, so changing them means "
            "re-encrypting, and this tool cannot recover a password it was not given.",
            progress,
        )

    try:
        src = resolve_source(source)
        require_pdf(src, op)
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
            # `allow=` spelled out here too. Without it pikepdf's default
            # applies and the encrypted file comes back forbidding
            # `modify_assembly` -- so encrypting a document quietly withdrew
            # permission to reorder its own pages, from the very caller who
            # holds the password. The password is the protection being asked
            # for; a restriction flag nobody requested is not part of it, and
            # `encrypt` and `permissions` producing different flag sets from
            # one library call was the tell.
            handle.save(
                destination, encryption=pikepdf.Encryption(owner=password, user=password, R=6, allow=ALL_ALLOWED)
            )
            progress.append(ok_step("encrypted with AES-256 (R=6)"))
        elif action == "decrypt":
            handle.save(destination)  # saving without encryption= drops it
            progress.append(ok_step("saved without encryption"))
        else:
            # Owner permissions only: the document stays encrypted for anyone
            # without the password, and the restriction flags that only ever
            # constrained a cooperating reader are cleared.
            #
            # Every flag spelled out rather than `pikepdf.Permissions()`. The
            # default constructor is not all-permitted -- it sets
            # `modify_assembly=False` -- so "cleared" left one restriction in
            # place, and the no-password branch that dropped encryption
            # entirely cleared it. One action, two different outcomes, neither
            # of them stated.
            handle.save(
                destination,
                encryption=pikepdf.Encryption(owner=password, user="", allow=ALL_ALLOWED, R=6),
            )
            progress.append(ok_step("permission flags cleared", "the document is still encrypted"))
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
    # What the file still FORBIDS, read back off the disk. `permissions` had only
    # a progress line saying "permission flags cleared" -- a report of what was
    # asked for, which is exactly what this repo refuses to accept from any
    # other tool. Naming the flags also makes the answer checkable by a reader
    # that is not this server, which is how the silent decrypt was found.
    restrictions = _permissions_of(destination, password)
    if restrictions is not None:
        result["restrictions"] = restrictions
    if action == "encrypt" and not encrypted_now:
        return fail(
            op,
            "The output was written but is not encrypted.",
            "This is a bug in the server, not in your call. Do not distribute the file.",
            progress,
            **result,
        )
    return ok(op, result, progress)


def _permissions_of(path, password: str) -> list[str] | None:
    """The restrictions still in force, by name. None where there are none.

    An unencrypted PDF carries no permission flags at all, and reporting eight
    `true`s for it would say "everything is explicitly allowed here" about a
    file that has no such statement in it.

    Opened without the password first, for the reason the main path above does
    it: pikepdf emits "A password was provided, but no password was needed to
    open this PDF" otherwise, and this function runs on every `protect` call
    including the `decrypt` one, whose output never needs a password. The first
    version of it printed that warning on every decrypt.
    """
    try:
        try:
            handle = pikepdf.open(path)
        except pikepdf.PasswordError:
            handle = pikepdf.open(path, password=password or "")
        with handle:
            if not handle.is_encrypted:
                return None
            return sorted(name for name, allowed in handle.allow._asdict().items() if not allowed)
    except pikepdf.PasswordError, pikepdf.PdfError:
        return None


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
        require_pdf(src, op)
        destination = resolve_out(out, src)
    except PathError as exc:
        return fail(op, str(exc), exc.hint, progress)

    # A caller's pattern is matched in a worker the server can stop
    # (shared/regex_guard): Python's re has no timeout, and a pattern with
    # nested repeats would otherwise hold this call for good. An escaped
    # literal cannot backtrack and stays in-process.
    try:
        matcher: re.Pattern[str] | Guard = (
            Guard(pattern, re.IGNORECASE, limit=budget.regex_seconds())
            if regex
            else re.compile(_literal(pattern), re.IGNORECASE)
        )
    except re.error as exc:
        return fail(
            op,
            f"{pattern!r} is not a valid regular expression: {exc}",
            "Pass regex=False to match it literally.",
            progress,
        )
    try:
        return _redact(op, src, destination, pages, matcher, progress)
    finally:
        if isinstance(matcher, Guard):
            matcher.close()


def _redact(op: str, src, destination, pages: str, matcher: re.Pattern[str] | Guard, progress: list[dict]) -> dict:
    """The redaction proper: strip the matching runs, write the file, then prove it."""
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
    beyond = 0
    touched: list[int] = []
    # Written beside the destination and moved into place only once verified:
    # a redaction that fails its check must not leave a file at the requested
    # path. It used to, with "Do not distribute this file" in the hint -- and a
    # file named *_redacted.pdf still carrying the name is exactly the file
    # someone distributes.
    staging = destination.with_name(f".{destination.stem}.unverified{destination.suffix}")
    try:
        for number in wanted:
            page = handle.pages[number - 1]
            count, collateral = _strip_from_page(handle, page, matcher)
            if count:
                removed += count
                beyond += collateral
                touched.append(number)
        handle.save(staging)
    except PatternTimeout as exc:
        handle.close()
        staging.unlink(missing_ok=True)
        return fail(
            op,
            str(exc),
            "Nothing was written. Rewrite the pattern without a quantifier inside a quantifier, "
            "or pass regex=False to match it literally.",
            progress,
            refused="budget",
            limit=f"{exc.limit:g}s",
        )
    except (pikepdf.PdfError, OSError) as exc:
        handle.close()
        staging.unlink(missing_ok=True)
        return fail(
            op, f"Could not write {destination.name}: {exc}", "Check the output directory is writable.", progress
        )
    handle.close()

    progress.append(info(f"removed {removed} text run(s) from {len(touched)} page(s)"))
    if beyond:
        progress.append(
            warn(
                f"{beyond} of those run(s) also drew text outside the match, and went with it",
                "this file sets several words in one run; a run is removed whole, never cut",
            )
        )

    # THE VERIFICATION. Re-extract the written file and confirm the pattern is
    # gone. Without this the tool is a claim; with it, it is a check. A
    # redaction that reports success it did not verify is the failure this
    # whole design exists to prevent.
    try:
        residual = _still_extractable(staging, matcher, wanted)
    except PatternTimeout as exc:
        staging.unlink(missing_ok=True)
        return fail(
            op,
            f"Redaction could not be verified: {exc}",
            "Nothing was written: the pattern ran out of time before the check finished.",
            progress,
            written=False,
            redacted=removed,
            verified=False,
            refused="budget",
            limit=f"{exc.limit:g}s",
        )
    result = {
        "out": str(destination),
        "redacted": removed,
        "removed_beyond_match": beyond,
        "pages": format_pages(touched),
        "verified": not residual,
        "residual_matches": len(residual),
    }
    if residual:
        staging.unlink(missing_ok=True)
        progress.append(warn("the pattern is still extractable from the output", f"pages {format_pages(residual)}"))
        return fail(
            op,
            f"Redaction could not be verified: the pattern is still extractable on page(s) {format_pages(residual)}. "
            "Nothing was written.",
            "The text is likely drawn with a font whose codes do not decode to the pattern; "
            "extract(pages=...) shows how the page reads.",
            progress,
            **{**result, "out": None, "written": False},
        )
    staging.replace(destination)
    finish(destination)
    progress.append(ok_step("verified: the pattern is no longer extractable"))
    return ok(op, result, progress)


def _strip_from_page(pdf: pikepdf.Pdf, page: pikepdf.Page, matcher: re.Pattern[str] | Guard) -> tuple[int, int]:
    """Delete the text-showing operators a match covers; return (removed, removed beyond the match).

    Operates on the content stream itself rather than drawing over the page,
    because drawing leaves the glyphs behind for any extractor to read. Tj and
    ' take a single string; TJ takes an array of strings and kerning numbers.

    **The pattern is matched across runs, not inside one.** Each run was tested
    on its own, and a producer that draws one glyph per operator -- 367 Tj on a
    SuperStore invoice, `V`, `i`, `v`, `e`, `k` -- never has a run holding
    "Vivek Grady", so nothing was removed and the check refused the file. The
    runs are now joined in stream order, the pattern is matched once over the
    whole, and every run the match touches goes. A run that also carries text
    outside the match goes whole too (a run is never cut, which would move the
    glyphs after the cut), and is counted as removed beyond the match.

    **The operand bytes are not the text.** They were read as Latin-1 here,
    which is true only of a PDF whose text is set in a base-14 font with no
    subsetting. LibreOffice, Word and every modern producer embed a SUBSET font
    and address it by glyph index, so each font's /ToUnicode CMap is used to turn
    glyph codes back into the text they draw, and the current font is tracked
    through Tf. Where a font has no /ToUnicode, the Latin-1 reading stands.

    `'` and `"` also move to the next line; a deleted one leaves its T* (and the
    spacing `"` sets) behind, so the lines under it do not move up.
    """
    decoders = _font_decoders(page)
    decode: Callable[[bytes], str] | None = None
    instructions: list = []
    runs: list[tuple[int, int, int]] = []
    parts: list[str] = []
    length = 0
    for operands, operator in pikepdf.parse_content_stream(page):
        name = str(operator)
        text: str | None = None
        if name == "Tf" and operands:
            # Tf is `/F1 12 Tf`: the resource name, then the size. Every
            # text-showing operator after it draws in that font until the next
            # one, which is why this cannot be decided per operator.
            decode = decoders.get(str(operands[0]))
        elif name in {"Tj", "'", '"'} and operands:
            text = _as_text(operands[-1], decode)
        elif name == "TJ" and operands:
            # A TJ operand is an Array of strings and kerning numbers, and the
            # numbers have to be SKIPPED rather than converted: `bytes(3)` is
            # three NUL bytes, not the digit 3. pyright types the operand as the
            # generic Object and does not know it is iterable. It is.
            text = "".join(
                _as_text(item, decode) or ""
                for item in operands[0]  # type: ignore[union-attr]
                if not isinstance(item, (int, float, Decimal))
            )
        if text:
            runs.append((len(instructions), length, length + len(text)))
            parts.append(text)
            length += len(text)
        instructions.append((operands, operator))
    spans = _spans(matcher, "".join(parts))
    doomed: dict[int, bool] = {}
    for index, start, end in runs:
        for lo, hi in spans:
            if start < hi and lo < end:
                doomed[index] = doomed.get(index, False) or start < lo or end > hi
    if not doomed:
        return 0, 0
    kept: list = []
    for index, (operands, operator) in enumerate(instructions):
        if index not in doomed:
            kept.append((operands, operator))
        elif str(operator) == "'":
            kept.append(([], pikepdf.Operator("T*")))
        elif str(operator) == '"':
            kept += [
                ([operands[0]], pikepdf.Operator("Tw")),
                ([operands[1]], pikepdf.Operator("Tc")),
                ([], pikepdf.Operator("T*")),
            ]
    page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(kept))
    return len(doomed), sum(doomed.values())


_OPEN, _CLOSE = "\x01", "\x02"


def _spans(matcher: re.Pattern[str] | Guard, text: str) -> list[tuple[int, int]]:
    """Where the pattern matches in `text`, as (start, end), empty matches dropped.

    The guard answers yes/no and substitutions, not positions, so each match is
    wrapped in two control characters by a substitution and the spans are read
    back from where they landed. The text is cleared of both characters first.
    """
    clean = text.replace(_OPEN, " ").replace(_CLOSE, " ")
    marked, count = matcher.subn(_OPEN + r"\g<0>" + _CLOSE, clean)
    if not count:
        return []
    spans: list[tuple[int, int]] = []
    position, start = 0, 0
    for char in marked:
        if char == _OPEN:
            start = position
        elif char == _CLOSE:
            if position > start:
                spans.append((start, position))
        else:
            position += 1
    return spans


def _literal(pattern: str) -> str:
    """A literal pattern whose words may be drawn with or without a space glyph between them."""
    words = pattern.split()
    return r"\s*".join(re.escape(word) for word in words) if words else re.escape(pattern)


def _as_text(operand, decode: Callable[[bytes], str] | None = None) -> str | None:
    try:
        raw = bytes(operand)
    except TypeError, ValueError:
        return None
    if decode is not None:
        return decode(raw)
    try:
        return raw.decode("latin-1")
    except ValueError, UnicodeDecodeError:
        return None


def _font_decoders(page: pikepdf.Page) -> dict[str, Callable[[bytes], str]]:
    """Resource name -> a bytes-to-text decoder, for each font that has one.

    Absent for a font with no /ToUnicode, so the caller falls back rather than
    decoding through a map that does not exist.
    """
    out: dict[str, Callable[[bytes], str]] = {}
    try:
        fonts = page.get("/Resources", {}).get("/Font", {})  # type: ignore[union-attr]
    except AttributeError, KeyError:
        return out
    for name, font in dict(fonts).items():  # type: ignore[arg-type]
        try:
            built = _tounicode_decoder(font)
        except Exception:  # noqa: BLE001 - a malformed CMap must not lose the page
            built = None
        if built is not None:
            out[str(name)] = built
    return out


# One entry of a bfrange: `<lo> <hi> <dst>` or `<lo> <hi> [<d1> <d2> ...]`.
# Written as one alternation scanned left to right rather than two passes,
# because a two-pass version matches three consecutive `<...>` tokens INSIDE an
# array as if they were a single-destination entry.
_BFRANGE = re.compile(
    r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(?:\[((?:\s*<[0-9A-Fa-f]+>)*)\s*\]|<([0-9A-Fa-f]+)>)",
    re.S,
)
_BFCHAR = re.compile(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")
_HEX = re.compile(r"<([0-9A-Fa-f]+)>")


def _tounicode_decoder(font) -> Callable[[bytes], str] | None:
    """Build a decoder from a font's /ToUnicode CMap, or None if it has none."""
    stream = font.get("/ToUnicode") if hasattr(font, "get") else None
    if stream is None:
        return None
    text = bytes(stream.read_bytes()).decode("latin-1", "replace")

    mapping: dict[int, str] = {}
    widths: set[int] = set()

    for block in re.findall(r"begincodespacerange(.*?)endcodespacerange", text, re.S):
        for low, _high in _BFCHAR.findall(block):
            widths.add(len(low) // 2)
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for source, destination in _BFCHAR.findall(block):
            widths.add(len(source) // 2)
            mapping[int(source, 16)] = _utf16be(destination)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for low, high, array, single in _BFRANGE.findall(block):
            widths.add(len(low) // 2)
            start, end = int(low, 16), int(high, 16)
            if array:
                for offset, destination in enumerate(_HEX.findall(array)):
                    mapping[start + offset] = _utf16be(destination)
            else:
                # A range's destination increments in its LAST code unit, so
                # <20> <2F> <0041> is A..P, not sixteen copies of A.
                for offset in range(end - start + 1):
                    mapping[start + offset] = _utf16be(single, offset)

    if not mapping:
        return None
    width = 2 if widths == {2} else 1

    def decode(raw: bytes) -> str:
        if width == 1:
            codes = list(raw)
        else:
            codes = [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw) - 1, 2)]
        # An unmapped code becomes U+FFFD rather than nothing. Dropping it
        # would close the gap and let a pattern match across a glyph that is
        # not there -- redacting text the caller never asked about.
        return "".join(mapping.get(code, "�") for code in codes)

    return decode


def _utf16be(hex_digits: str, offset: int = 0) -> str:
    raw = bytes.fromhex(hex_digits if len(hex_digits) % 2 == 0 else "0" + hex_digits)
    if offset and len(raw) >= 2:
        value = (int.from_bytes(raw[-2:], "big") + offset) & 0xFFFF
        raw = raw[:-2] + value.to_bytes(2, "big")
    return raw.decode("utf-16-be", "replace")


def _still_extractable(path, matcher: re.Pattern[str] | Guard, pages: list[int]) -> list[int]:
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
