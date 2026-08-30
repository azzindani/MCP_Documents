"""Email -> core.ir.Document, via the standard library's `email` package.

No third-party dependency: `email.policy.default` parses RFC 5322 properly,
including the encoded-word headers (`=?utf-8?B?...?=`) and multipart bodies
that every real message has. A regex over the raw file would get the common
case right and the international subject line wrong.

**The body is preferred as plain text and falls back to HTML.** A multipart
message carries both, they say the same thing, and returning both doubles the
tokens for nothing. Where only HTML exists it is run through this package's own
HTML reader rather than a second, worse stripper -- one implementation of "turn
markup into text", shared.

Attachments are LISTED, never extracted. Names, types and sizes are exactly
what a caller needs to decide what to do next, and writing files to disk as a
side effect of reading a message is not something a read tool should do. To
read one, save it and call probe() on it.

`.msg` (Outlook's own format) is registered here but is not RFC 5322 -- it is
an OLE compound file. It is refused with that as the reason rather than parsed
into nonsense, because a wrong answer about a document is worse than none.
"""

from __future__ import annotations

from core.ir import Basis, Document
from core.readers import _flow

BODY_SIZE = 12.0
HEADER_SIZE = 22.0

# Headers worth a caller's tokens, in the order a person reads them.
HEADERS = ("from", "to", "cc", "date", "subject")


def open_document(path: str, password: str = "") -> Document:
    """Read an .eml message: headers, body, and a list of attachments."""
    import email
    from email import policy

    src = _flow.guard_size(path)
    if src.suffix.lower() == ".msg":
        from core.readers import ReaderError

        raise ReaderError(
            f"{src.name} is an Outlook .msg file, which is an OLE compound file rather than RFC 5322 mail.",
            "Export it from Outlook as .eml, or convert(to='pdf') if you only need to read it.",
        )

    with src.open("rb") as handle:
        message = email.message_from_binary_file(handle, policy=policy.default)

    blocks = []
    subject = str(message.get("subject", "") or "").strip()
    if subject:
        blocks.append(_flow.block(subject, "heading", "native", HEADER_SIZE))

    header_lines = [f"{name.title()}: {message[name]}" for name in HEADERS if message[name]]
    if header_lines:
        blocks.append(_flow.block("\n".join(header_lines), "head_foot", "native", BODY_SIZE))

    body, body_basis = _body(message)
    for paragraph in _paragraphs(body):
        blocks.append(_flow.block(paragraph, "para", body_basis, BODY_SIZE))

    attachments = _attachments(message)
    if attachments:
        listing = "\n".join(f"{a['filename']} ({a['content_type']}, {a['bytes']:,} bytes)" for a in attachments)
        blocks.append(_flow.block(f"Attachments:\n{listing}", "caption", "native", BODY_SIZE))

    meta = {
        "subject": subject,
        "from": str(message.get("from", "") or ""),
        "to": str(message.get("to", "") or ""),
        "date": str(message.get("date", "") or ""),
        "attachments": attachments,
        "attachment_count": len(attachments),
        "body_basis": body_basis,
    }
    return _flow.build(str(src), "eml", blocks, meta)


def _body(message) -> tuple[str, Basis]:
    """The message body as text, and how it was obtained.

    `get_body` walks the multipart structure the way a mail client does, rather
    than taking the first part and hoping. The basis distinguishes a body the
    message really sent as text from one recovered out of its HTML alternative,
    because the second has lost the sender's formatting and may have lost
    content that lived only in markup.
    """
    plain = message.get_body(preferencelist=("plain",))
    if plain is not None:
        return _content(plain), "native"

    html_part = message.get_body(preferencelist=("html",))
    if html_part is not None:
        return _html_to_text(_content(html_part)), "native"

    if not message.is_multipart():
        return _content(message), "native"
    return "", "empty"


def _content(part) -> str:
    try:
        payload = part.get_content()
    except LookupError, UnicodeDecodeError:
        # An unknown charset or a broken transfer encoding. The bytes are still
        # a message; decoding them loosely beats losing them.
        raw = part.get_payload(decode=True) or b""
        return raw.decode("utf-8", errors="replace")
    return payload if isinstance(payload, str) else str(payload)


def _html_to_text(markup: str) -> str:
    """Reuse this package's HTML reader rather than write a second stripper."""
    import lxml.html

    from core.readers.html import DROP_TAGS

    try:
        tree = lxml.html.document_fromstring(markup)
    except Exception:
        return markup
    for tag in DROP_TAGS:
        for element in tree.iter(tag):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
    return "\n".join(line.strip() for line in tree.text_content().splitlines() if line.strip())


def _paragraphs(body: str) -> list[str]:
    """Blank-line separated runs, quoted replies kept.

    A quoted reply (`> ...`) is left in place rather than stripped. Trimming it
    is a judgement about what the caller wants, and on a thread where the
    question is in the quote and the answer is one line, stripping it removes
    the content.
    """
    out: list[str] = []
    buffer: list[str] = []
    for line in body.splitlines():
        if line.strip():
            buffer.append(line.rstrip())
        elif buffer:
            out.append("\n".join(buffer))
            buffer = []
    if buffer:
        out.append("\n".join(buffer))
    return out


def _attachments(message) -> list[dict]:
    found: list[dict] = []
    for part in message.iter_attachments():
        payload = part.get_payload(decode=True) or b""
        found.append(
            {
                "filename": part.get_filename() or "(unnamed)",
                "content_type": part.get_content_type(),
                "bytes": len(payload),
            }
        )
    return found


def probe_extras(doc: Document) -> dict:
    keys = ("subject", "from", "to", "date", "attachment_count", "body_basis")
    return {key: doc.meta[key] for key in keys if key in doc.meta}


close_document = _flow.close_document
load_page = _flow.load_page
load_page_words = _flow.load_page
