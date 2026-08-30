"""protect(action='permissions') decrypted the document when given no password.

Permission flags live inside a PDF's encryption dictionary, so changing them
means re-encrypting, which needs the owner password. Without one the code fell
through to `encryption=False` -- and pikepdf saving without an `encryption=`
argument writes an UNENCRYPTED file.

Handed a filing encrypted with printing and copying forbidden, the call
returned:

    success: true, action: "permissions", encrypted: false
    progress: ["permission flags cleared"]

and wrote a document any reader opens with no password at all. The protection
was gone, the action was named after the flags, and the progress line described
the flags. One field told the truth -- `encrypted: false` -- with nothing
around it to say that was the point.

Found by reading the output back with pypdfium2 rather than with pikepdf, which
is what wrote it. A writer and its own reader agree with each other.

Two more things fell out of writing the report field, and both come from one
habit: `pikepdf.Permissions()` reads like "no restrictions" and is not.

  * It sets `modify_assembly=False`. So `protect(action='encrypt')`, which
    passed no `allow=` at all, quietly withdrew permission to reorder the
    document's own pages -- from the caller holding the password.
  * `permissions` cleared seven of the eight flags for the same reason, while
    its no-password path (the one that dropped encryption) cleared all eight.
    One action, two outcomes, neither stated anywhere.

And the docstring said "set permissions" for a tool that takes no permissions
argument and can only ever clear them.
"""

from __future__ import annotations

import pikepdf
import pypdfium2 as pdfium
import pytest

from servers.docs_edit import engine as edit
from tests.fixtures import build

PASSWORD = "secret"
ALL_FLAGS = {
    "accessibility",
    "extract",
    "modify_annotation",
    "modify_assembly",
    "modify_form",
    "modify_other",
    "print_lowres",
    "print_highres",
}


@pytest.fixture(scope="module")
def restricted(tmp_path_factory):
    """The shape a publisher ships: opens without a password, forbids printing."""
    source = build.born_digital(pages=2, name="perm_source.pdf")
    out = tmp_path_factory.mktemp("perm") / "restricted.pdf"
    with pikepdf.open(source) as handle:
        handle.save(
            out,
            encryption=pikepdf.Encryption(
                owner=PASSWORD,
                user="",
                R=6,
                allow=pikepdf.Permissions(extract=False, print_lowres=False, print_highres=False),
            ),
        )
    return out


def opens_without_password(path) -> bool:
    try:
        pdfium.PdfDocument(str(path))
        return True
    except Exception:  # noqa: BLE001
        return False


class TestItWillNotSilentlyRemoveTheEncryption:
    def test_permissions_without_a_password_is_refused(self, restricted, tmp_path):
        payload = edit.protect(str(restricted), "permissions", out=str(tmp_path / "out.pdf"))
        assert not payload["success"], payload
        assert "password" in payload["error"]

    def test_and_writes_no_file(self, restricted, tmp_path):
        destination = tmp_path / "out.pdf"
        edit.protect(str(restricted), "permissions", out=str(destination))
        assert not destination.exists(), "a refusal that still writes the file has refused nothing"

    def test_the_hint_says_why_a_password_is_needed_at_all(self, restricted, tmp_path):
        payload = edit.protect(str(restricted), "permissions", out=str(tmp_path / "out.pdf"))
        assert "encryption" in payload["hint"]

    def test_the_source_is_still_encrypted_afterwards(self, restricted, tmp_path):
        edit.protect(str(restricted), "permissions", out=str(tmp_path / "out.pdf"))
        assert not opens_without_password(restricted) or pikepdf.open(restricted).is_encrypted


class TestWithAPasswordItClearsTheFlagsAndKeepsTheLock:
    def test_the_document_is_still_encrypted(self, restricted, tmp_path):
        destination = tmp_path / "cleared.pdf"
        payload = edit.protect(str(restricted), "permissions", password=PASSWORD, out=str(destination))
        assert payload["success"], payload
        assert payload["result"]["encrypted"] is True

    def test_every_restriction_is_gone(self, restricted, tmp_path):
        destination = tmp_path / "cleared.pdf"
        payload = edit.protect(str(restricted), "permissions", password=PASSWORD, out=str(destination))
        assert payload["result"]["restrictions"] == []

    def test_a_second_library_agrees(self, restricted, tmp_path):
        """pikepdf wrote it, so pikepdf is not the witness that counts."""
        destination = tmp_path / "cleared.pdf"
        edit.protect(str(restricted), "permissions", password=PASSWORD, out=str(destination))
        with pikepdf.open(destination) as handle:
            assert handle.is_encrypted
            assert all(handle.allow._asdict().values()), handle.allow
        assert opens_without_password(destination), "the user password was empty before and must stay empty"

    def test_the_progress_line_says_it_stayed_encrypted(self, restricted, tmp_path):
        payload = edit.protect(str(restricted), "permissions", password=PASSWORD, out=str(tmp_path / "c.pdf"))
        assert "still encrypted" in " ".join(str(step) for step in payload["progress"])


class TestEncryptDoesNotAddARestrictionNobodyAskedFor:
    def test_it_leaves_nothing_forbidden(self, tmp_path):
        source = build.born_digital(pages=1, name="enc_source.pdf")
        destination = tmp_path / "enc.pdf"
        payload = edit.protect(str(source), "encrypt", password=PASSWORD, out=str(destination))
        assert payload["success"], payload
        assert payload["result"]["restrictions"] == [], "modify_assembly came back False from a bare Permissions()"

    def test_the_password_still_protects_it(self, tmp_path):
        source = build.born_digital(pages=1, name="enc_source.pdf")
        destination = tmp_path / "enc.pdf"
        edit.protect(str(source), "encrypt", password=PASSWORD, out=str(destination))
        assert not opens_without_password(destination)
        assert len(pdfium.PdfDocument(str(destination), password=PASSWORD)) == 1

    def test_encrypt_and_permissions_agree_on_the_flags(self, restricted, tmp_path):
        """One library call, two callers, and they used to disagree by one flag."""
        source = build.born_digital(pages=1, name="enc_source.pdf")
        a = tmp_path / "a.pdf"
        b = tmp_path / "b.pdf"
        edit.protect(str(source), "encrypt", password=PASSWORD, out=str(a))
        edit.protect(str(restricted), "permissions", password=PASSWORD, out=str(b))
        with pikepdf.open(a, password=PASSWORD) as ha, pikepdf.open(b) as hb:
            assert ha.allow._asdict() == hb.allow._asdict()
            assert set(ha.allow._asdict()) == ALL_FLAGS


class TestDecryptIsUnchanged:
    def test_it_reports_no_restrictions_for_an_unencrypted_file(self, tmp_path):
        """None, not an empty list: a plain PDF makes no permission statement."""
        source = build.born_digital(pages=1, name="dec_source.pdf")
        enc, dec = tmp_path / "e.pdf", tmp_path / "d.pdf"
        edit.protect(str(source), "encrypt", password=PASSWORD, out=str(enc))
        payload = edit.protect(str(enc), "decrypt", password=PASSWORD, out=str(dec))
        assert payload["result"]["encrypted"] is False
        assert "restrictions" not in payload["result"]
        assert opens_without_password(dec)
