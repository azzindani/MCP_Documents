"""External binaries, and saying honestly when one is not there.

Three tools here need something that is not a Python package: `convert` needs
LibreOffice for anything -> PDF, `ocr` needs Tesseract, and `optimize`'s
strongest compression needs Ghostscript. None of them can be pip-installed, all
of them are absent on a plain machine, and a server that pretends otherwise
fails at the worst moment with a message about a subprocess.

So capability is checked before the work, and a missing binary produces a
refusal that names the binary and the operation it blocks -- not a
FileNotFoundError from deep inside a subprocess call. A caller can act on
"Tesseract is not installed"; nobody can act on errno 2.

The same check drives the smoke tests: a tool whose binary is absent is
reported as unavailable rather than broken, which is the difference between a
deployment gap and a defect.
"""

from __future__ import annotations

import shutil
import subprocess

# name -> (candidate executables, what stops working without it, how to get it)
BINARIES: dict[str, tuple[tuple[str, ...], str, str]] = {
    "libreoffice": (
        ("soffice", "libreoffice"),
        "convert(to='pdf') from docx, xlsx, pptx or html",
        "apt-get install libreoffice-core libreoffice-writer",
    ),
    "tesseract": (
        ("tesseract",),
        "ocr()",
        "apt-get install tesseract-ocr tesseract-ocr-eng",
    ),
    "ghostscript": (
        ("gs",),
        "optimize(action='compress') at its strongest setting",
        "apt-get install ghostscript",
    ),
    "qpdf": (
        ("qpdf",),
        "nothing -- pikepdf embeds QPDF, so this is only for diagnostics",
        "apt-get install qpdf",
    ),
}


def which(name: str) -> str | None:
    """Path to a known binary, or None. Looked up at CALL time, not import.

    A container that gains Tesseract on a rebuild should not need the process
    restarted to notice, and a test that stubs PATH should work without a
    module reload.
    """
    candidates, _, _ = BINARIES[name]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def available(name: str) -> bool:
    return which(name) is not None


def missing_reason(name: str) -> tuple[str, str]:
    """(error, hint) for a binary that is not installed.

    The hint names the package rather than the concept. "Install an OCR engine"
    is not actionable; the apt line is.
    """
    _, blocks, install = BINARIES[name]
    return (
        f"{name} is not installed on this server, so {blocks} is unavailable.",
        f"Install it with: {install}",
    )


def capabilities() -> dict[str, bool]:
    """What this deployment can actually do. Reported by probe-adjacent tools
    so a caller can see the gap before hitting it."""
    return {name: available(name) for name in BINARIES}


def run(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run a binary with a timeout, capturing both streams.

    A timeout, always. LibreOffice in particular hangs rather than failing when
    it dislikes a document, and an MCP tool that never returns is worse than
    one that refuses -- the caller cannot tell it from a dead connection.
    """
    return subprocess.run(argv, capture_output=True, timeout=timeout, check=False, text=True)
