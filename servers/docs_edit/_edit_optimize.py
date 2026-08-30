"""optimize() and ocr().

`optimize` is the structural housekeeping a PDF site offers: compress, repair,
linearise. All of it through QPDF, which pikepdf embeds, so none of it needs a
binary that might be absent.

`ocr` does. Tesseract is not a Python package and cannot be pip-installed, so
this refuses with the package name when it is missing rather than raising a
FileNotFoundError from inside a subprocess -- a caller can act on the first and
nobody can act on errno 2.

**The time budget lives here.** Tesseract runs 1-3 seconds per page per core,
so a 400-page scan is fourteen minutes against a client timeout measured in
seconds. The estimate is checked BEFORE the work starts: a call that runs for
ten minutes and is then killed has spent the time and lost the result, which is
strictly worse than a refusal naming a page range.
"""

from __future__ import annotations

import os
from pathlib import Path

import pikepdf

from core import binaries, budget
from core.formatter import fail, ok, refuse
from core.paths import PathError, finish, require_pdf, resolve_out, resolve_source
from core.selection import SelectionError, format_pages, parse_pages
from shared.progress import info
from shared.progress import ok as ok_step

ACTIONS = ("compress", "repair", "linearize")


def optimize(source: str, action: str = "compress", out: str = "") -> dict:
    """Compress, repair or linearise a PDF. Reports the real size change."""
    op = "optimize"
    progress: list[dict] = []
    if action not in ACTIONS:
        return fail(op, f"{action!r} is not an action this tool has.", f"Use one of: {', '.join(ACTIONS)}.", progress)

    try:
        src = resolve_source(source)
        require_pdf(src, op)
        destination = resolve_out(out, src)
    except PathError as exc:
        return fail(op, str(exc), exc.hint, progress)

    before = src.stat().st_size
    try:
        # QPDF recovers a file whose cross-reference table is damaged or whose
        # tail is truncated, which is exactly what repair means here: measured
        # on the corpus, the fast reader refuses a truncated file and QPDF
        # opens it and keeps every page.
        handle = pikepdf.open(src, allow_overwriting_input=False)
    except pikepdf.PasswordError:
        return fail(
            op,
            f"{src.name} is encrypted.",
            "Run protect(action='decrypt', password='...') first.",
            progress,
        )
    except pikepdf.PdfError as exc:
        return fail(
            op,
            f"{src.name} could not be opened even for repair: {exc}",
            "The file may not be a PDF at all. Check it with probe().",
            progress,
        )

    pages = len(handle.pages)
    try:
        if action == "compress":
            # Object streams and stream recompression: the safe, permissive
            # part of what a PDF site's "compress" does. The large wins on a
            # scanned document come from downsampling its images, which needs
            # Ghostscript -- see the note in the response rather than silently
            # under-delivering against the word "compress".
            # ObjectStreamMode.generate is the correct enum member; pyright reads
            # its value as a bare int through pikepdf's stub.
            handle.save(
                destination,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,  # type: ignore[arg-type]
            )
        elif action == "linearize":
            handle.save(destination, linearize=True)
        else:
            handle.save(destination)
        finish(destination)
    except (pikepdf.PdfError, OSError) as exc:
        return fail(
            op, f"Could not write {destination.name}: {exc}", "Check the output directory is writable.", progress
        )
    finally:
        handle.close()

    after = destination.stat().st_size
    progress.append(ok_step(f"{action} wrote {pages} page(s)"))

    result = {
        "out": str(destination),
        "action": action,
        "pages": pages,
        # Real byte counts on both sides, never a rounded kilobyte figure. A
        # sibling repo divided by 1024 and made every sub-kilobyte file "0 KB",
        # including inside a delete confirmation.
        "bytes_before": before,
        "bytes_after": after,
        "ratio": round(after / before, 4) if before else None,
    }
    if action == "compress":
        if after >= before:
            # An honest non-result. Reporting "compressed" for a file that grew
            # is the claim-its-own-numbers-do-not-support shape.
            result["note"] = "already compact; this file did not get smaller"
        if not binaries.available("ghostscript"):
            result["note_images"] = (
                "image downsampling is unavailable (ghostscript not installed), "
                "so a scanned document will compress little here"
            )
    return ok(op, result, progress)


def ocr(source: str, pages: str = "", language: str = "eng", out: str = "") -> dict:
    """Add a searchable text layer to scanned pages. Page range required."""
    op = "ocr"
    progress: list[dict] = []

    # The ARGUMENT is checked before the environment, and the order matters.
    # Checked the other way round, ocr() on an HTML file answered "install
    # tesseract-ocr" on any machine without Tesseract -- so the caller installs
    # a 400 MB package, runs it again, and only then learns the real problem is
    # that this tool takes PDFs. The hint must name what actually went wrong,
    # and "your argument is the wrong format" is true regardless of what is
    # installed. Found by CI, which is the only place here without Tesseract.
    try:
        src = resolve_source(source)
        require_pdf(src, op)
        destination = resolve_out(out, src)
    except PathError as exc:
        return fail(op, str(exc), exc.hint, progress)

    if not binaries.available("tesseract"):
        error, hint = binaries.missing_reason("tesseract")
        return fail(op, error, hint, progress, available=False)

    from core.readers import ReaderError, load_page, open_source

    try:
        doc = open_source(str(src))
        wanted = parse_pages(pages, doc.page_count)
    except (SelectionError, ReaderError) as exc:
        return fail(op, str(exc), exc.hint, progress)

    # An empty `pages` means "the pages that actually need it", not "all of
    # them". OCR'ing a page that already has a text layer costs seconds and
    # replaces good text with worse text.
    if not pages.strip():
        wanted = [n for n in wanted if load_page(doc, n).is_scanned]
        if not wanted:
            return ok(
                op,
                {"out": str(src), "pages_ocred": 0, "pages": ""},
                progress,
                basis="text_layer",
                note="every page already has a text layer; nothing to do",
            )
        progress.append(info(f"selected the {len(wanted)} page(s) with no text layer"))

    estimate = len(wanted) * budget.ocr_seconds_per_page()
    ceiling = budget.max_ocr_seconds()
    if estimate > ceiling:
        fits = max(1, int(ceiling / budget.ocr_seconds_per_page()))
        return refuse(
            op,
            f"{len(wanted)} page(s) would take about {estimate / 60:.1f} minutes, over the {ceiling:.0f}s limit.",
            f"Run ocr(pages='{format_pages(wanted[:fits])}') and repeat for the rest. "
            "probe() reports which pages are scanned.",
            limit=f"{ceiling:.0f}s",
            seen=f"~{estimate:.0f}s",
            progress=progress,
        )

    # Rasterise -> Tesseract -> re-embed, page by page.
    #
    # Tesseract's own `pdf` output mode is used rather than its plain text
    # mode, because it writes a PDF whose invisible text layer sits at the
    # glyph positions it recognised. Taking the text and stamping it at the top
    # of the page would produce a searchable document whose text has no
    # relationship to what is on it -- searchable and wrong, which is worse
    # than not searchable.
    import subprocess
    import tempfile

    import pypdfium2 as pdfium

    # 200, not the conventional 300. Measured on a real scanned invoice, 300
    # DPI cost 15.2s against 3.9s and recognised 221 words against 228 -- five
    # times slower for slightly worse text. 300 is the right default for a
    # clean flatbed scan and the wrong one for a photograph, and photographs
    # are what "scanned invoice" means in practice.
    dpi = int(os.environ.get("DOCS_OCR_DPI", "200"))
    scale = dpi / 72.0
    rendered = 0
    handle = pdfium.PdfDocument(str(src))
    try:
        with tempfile.TemporaryDirectory() as scratch:
            work = Path(scratch)
            produced: dict[int, Path] = {}
            for number in wanted:
                page = handle[number - 1]
                # See _edit_convert: scale is documented float, annotated int.
                image = page.render(scale=scale).to_pil()  # type: ignore[arg-type]
                png = work / f"p{number}.png"
                image.save(png)
                stem = work / f"p{number}_ocr"
                result = binaries.run(
                    ["tesseract", str(png), str(stem), "-l", language, "--dpi", str(dpi), "pdf"],
                    timeout=budget.ocr_page_timeout(),
                )
                if result.returncode != 0:
                    return fail(
                        op,
                        f"Tesseract failed on page {number}: {result.stderr.strip()[:200]}",
                        f"Check that the {language!r} language data is installed "
                        f"(apt-get install tesseract-ocr-{language}).",
                        progress,
                    )
                produced[number] = stem.with_suffix(".pdf")
                rendered += 1

            # Replace only the pages that were OCR'd; every other page keeps its
            # original bytes. Rebuilding the whole document from renders would
            # turn a hybrid file's good text pages into images.
            with pikepdf.open(src) as original:
                for number, ocr_pdf in produced.items():
                    with pikepdf.open(ocr_pdf) as layered:
                        original.pages[number - 1] = layered.pages[0]
                original.save(destination)
            finish(destination)
    except (pikepdf.PdfError, OSError, subprocess.TimeoutExpired) as exc:
        return fail(op, f"OCR failed: {exc}", "Try a smaller page range with ocr(pages='...').", progress)
    finally:
        handle.close()

    progress.append(ok_step(f"OCR'd {rendered} page(s) at {dpi} DPI"))

    # Verify by reading the result back, rather than reporting what Tesseract
    # was asked to do. A page that came out with no text layer is a failed OCR
    # however cleanly the subprocess exited.
    from core.readers.pdf import _text_layer_basis

    check = pdfium.PdfDocument(str(destination))
    try:
        still_empty = [
            n for n in wanted if _text_layer_basis(check[n - 1].get_textpage().get_text_bounded()) == "empty"
        ]
    finally:
        check.close()

    result = {
        "out": str(destination),
        "pages_ocred": rendered,
        "pages": format_pages(wanted),
        "dpi": dpi,
        "language": language,
        "pages_still_without_text": format_pages(still_empty),
    }
    if still_empty:
        progress.append(info(f"{len(still_empty)} page(s) produced no text"))
        result["note"] = "some pages produced no recognisable text; they may be images without writing"
    return ok(op, result, progress, basis="ocr")
