"""convert() -- one tool absorbing ten of a PDF site's buttons.

The two directions are not symmetric and this tool says so rather than
flattening them into one word.

    TO pdf      from docx, xlsx, pptx, html, md, txt -- LibreOffice. A solved
                problem, high fidelity, and the reason a PDF site's "Word to
                PDF" is boring.

    FROM any    to txt, md, html -- this repo's own readers and cleaner, so
                every format core/readers handles is a valid source. Lossy
                targets, and the loss is the point.
                `images` is the exception and still needs a PDF: it goes
                through PDFium, and an email has nothing to rasterise.

    FROM pdf    to docx, xlsx, pptx -- RECONSTRUCTION from glyphs at
                coordinates. Not implemented here, and refused with the reason
                rather than shipped badly. See below.

**Never route pdf -> docx through LibreOffice's Draw import filter.** It
produces a document in which every line of text is a separate floating text
box. It opens, it contains the right words, it is unusable, and it reports
success -- the exact defect shape this fleet's sweeps exist to catch. A caller
who asked for an editable document and got 400 text boxes has been given a
worse answer than a refusal.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from core import binaries, budget
from core.formatter import fail, ok, refuse
from core.paths import PathError, finish, resolve_out, resolve_source
from shared.progress import info, warn
from shared.progress import ok as ok_step

OP = "convert"

# What LibreOffice can turn into a PDF. Deliberately a list rather than "try it
# and see": an unsupported input makes soffice hang rather than fail, and a
# tool that never returns is worse than one that refuses.
TO_PDF_INPUTS = {
    ".docx",
    ".doc",
    ".odt",
    ".rtf",
    ".xlsx",
    ".xls",
    ".ods",
    ".pptx",
    ".ppt",
    ".odp",
    ".html",
    ".htm",
    ".txt",
    ".md",
    ".csv",
}

# Targets produced by reading the source and writing it out again. Every one
# of these works for every format core/readers can read -- except `images`,
# which rasterises and so still needs a PDF.
TEXT_TARGETS = {"txt", "md", "html", "images"}
RECONSTRUCTION_TARGETS = {"docx", "xlsx", "pptx"}
TARGETS = TEXT_TARGETS | RECONSTRUCTION_TARGETS | {"pdf"}

# LibreOffice's first run initialises a profile and is far slower than the
# rest. A timeout below that turns a working conversion into a mystery.
SOFFICE_TIMEOUT_S = 180.0


def convert(source: str, to: str, out: str = "") -> dict:
    """Convert between formats: pdf, txt, md, html, images."""
    progress: list[dict] = []
    target = to.strip().lower().lstrip(".")
    if target not in TARGETS:
        return fail(
            OP,
            f"{to!r} is not a target this tool has.",
            f"Use one of: {', '.join(sorted(TARGETS))}.",
            progress,
        )

    try:
        src = resolve_source(source)
    except PathError as exc:
        return fail(OP, str(exc), exc.hint, progress)

    if target == "pdf":
        return _to_pdf(src, out, progress)
    if target in RECONSTRUCTION_TARGETS:
        return _refuse_reconstruction(src, target, progress)

    # Rendering is the one direction that still needs a PDF: it goes through
    # PDFium, and there is nothing to rasterise in an email or a CSV.
    if target == "images" and src.suffix.lower() != ".pdf":
        return fail(
            OP,
            f"Rendering {src.suffix or 'that'} to images is not supported.",
            f"Run convert(source='{src.name}', to='pdf') first, then convert(to='images') on the result.",
            progress,
        )

    # Everything else goes through the reader layer, so it works for every
    # format that layer can read -- not only PDF. This used to refuse anything
    # but a PDF outright, which was a leftover from when `pdf` was the only
    # reader; `to_markdown` of an HTML file or a docx is exactly the kind of
    # thing this tool is for, and it needed no new code, only the removal of a
    # check that had stopped being true.
    from core.readers import READERS

    if src.suffix.lower() not in READERS:
        return fail(
            OP,
            f"There is no reader for {src.suffix or 'a file with no extension'}, so it cannot become {target}.",
            f"Readers available: {', '.join(sorted(READERS))}. Use convert(to='pdf') first for anything else.",
            progress,
        )
    return _from_source(src, target, out, progress)


def _to_pdf(src: Path, out: str, progress: list[dict]) -> dict:
    if src.suffix.lower() == ".pdf":
        return fail(
            OP,
            f"{src.name} is already a PDF.",
            "Use optimize() to rewrite it, or assemble() to restructure it.",
            progress,
        )
    if src.suffix.lower() not in TO_PDF_INPUTS:
        return fail(
            OP,
            f"{src.suffix!r} cannot be converted to PDF here.",
            "Supported inputs: " + ", ".join(sorted(TO_PDF_INPUTS)) + ".",
            progress,
        )
    if not binaries.available("libreoffice"):
        error, hint = binaries.missing_reason("libreoffice")
        return fail(OP, error, hint, progress, available=False)

    try:
        destination = resolve_out(out, src, ".pdf")
    except PathError as exc:
        return fail(OP, str(exc), exc.hint, progress)

    soffice = binaries.which("libreoffice")
    with tempfile.TemporaryDirectory() as scratch:
        try:
            result = binaries.run(
                [str(soffice), "--headless", "--convert-to", "pdf", "--outdir", scratch, str(src)],
                timeout=SOFFICE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return fail(
                OP,
                f"LibreOffice did not finish converting {src.name} within {SOFFICE_TIMEOUT_S:.0f}s.",
                "It hangs rather than failing on a document it cannot read. Try opening and re-saving the file.",
                progress,
            )
        produced = next(Path(scratch).glob("*.pdf"), None)
        if result.returncode != 0 or produced is None:
            return fail(
                OP,
                f"LibreOffice could not convert {src.name}: {(result.stderr or result.stdout).strip()[:200]}",
                "Check the file opens in a normal application first.",
                progress,
            )
        shutil.move(str(produced), destination)
    finish(destination)

    # Read the result back rather than trusting the exit code. LibreOffice
    # returns 0 for conversions that produced an empty document.
    import pypdfium2 as pdfium

    handle = pdfium.PdfDocument(str(destination))
    pages = len(handle)
    handle.close()
    if pages == 0:
        return fail(
            OP,
            "The conversion produced a PDF with no pages.",
            "The source may be empty or password-protected.",
            progress,
        )

    progress.append(ok_step(f"converted {src.suffix} to PDF, {pages} page(s)"))
    return ok(
        OP,
        {"out": str(destination), "to": "pdf", "pages": pages, "bytes": destination.stat().st_size},
        progress,
        basis="native",
    )


def _from_source(src: Path, target: str, out: str, progress: list[dict]) -> dict:
    """Any readable format -> txt, md or html, through the reader layer.

    Named `_from_pdf` when PDF was the only reader. It never contained
    anything PDF-specific: it calls the docs-read tools, which route by
    extension, so the whole of this works for HTML, Word, slides, email and
    epub the moment those readers exist.
    """
    from servers.docs_read import engine as read_engine

    if target == "images":
        return _to_images(src, out, progress)

    reader = read_engine.to_markdown if target == "md" else read_engine.extract
    payload = reader(str(src))
    if not payload["success"]:
        # Pass the read tool's own refusal through unchanged rather than
        # rewriting it. Its hint already names the page range that would work,
        # and a second tool paraphrasing it is how hints stop matching errors.
        return fail(
            OP,
            payload["error"],
            payload["hint"],
            progress,
            **{k: v for k, v in payload.items() if k in {"limit", "seen", "refused"}},
        )

    body = payload["result"].get("markdown") or payload["result"].get("text", "")
    if target == "html":
        body = _as_html(src.stem, body)

    try:
        destination = resolve_out(out, src, f".{target}")
    except PathError as exc:
        return fail(OP, str(exc), exc.hint, progress)
    destination.write_text(body, encoding="utf-8")
    finish(destination)

    progress.append(ok_step(f"wrote {len(body):,} characters of {target}"))
    return ok(
        OP,
        {"out": str(destination), "to": target, "characters": len(body), "bytes": destination.stat().st_size},
        progress,
        basis=payload.get("basis", "text_layer"),
    )


def _to_images(src: Path, out: str, progress: list[dict]) -> dict:
    import pypdfium2 as pdfium

    handle = pdfium.PdfDocument(str(src))
    try:
        first = handle[0]
        width, height = first.get_size()
        # The ceiling and the setting are two different numbers. `dpi_that_fits`
        # answers "what is the largest render that stays inside the memory
        # budget", and using its answer as the resolution made the resolution a
        # property of the BATCH: 600 DPI for one page, 423 for four, 189 for
        # twenty, 62 for the whole filing. Page 6 of the same document came back
        # 5100 x 6601 asked for on its own and 3596 x 4653 asked for with three
        # neighbours -- nothing about the page changed, only its company.
        ceiling = budget.dpi_that_fits(width, height, len(handle))
        if ceiling < 72:
            # The hint has to name something that can actually be done. It used
            # to say "accept N DPI by rendering a range with read_page()", and
            # read_page() renders nothing -- it returns text, tables and links,
            # and has no DPI. `convert` has no `pages` parameter either, so
            # "render fewer pages" was not available through the tool that had
            # just refused. Both halves were impossible.
            fits = budget.pages_that_fit_render(width, height, budget.render_dpi())
            return refuse(
                OP,
                f"Rendering {len(handle)} page(s) does not fit the memory budget at a usable resolution.",
                f"Take a range first, then render it: assemble(sources=['{src}'], "
                f"select='{src.stem}:1-{fits}', out='part.pdf') then convert(source='part.pdf', to='images'). "
                f"{fits} page(s) of this size fit at {budget.render_dpi()} DPI.",
                limit=f"{budget.max_render_bytes():,} bytes",
                seen=f"{len(handle)} pages",
                progress=progress,
            )
        dpi = min(budget.render_dpi(), ceiling)
        if dpi < budget.render_dpi():
            progress.append(
                warn(
                    f"rendering at {dpi} DPI rather than {budget.render_dpi()}",
                    f"{len(handle)} pages at once do not fit the {budget.max_render_bytes():,} byte render budget",
                )
            )
        try:
            directory = resolve_out(out or f"{src.stem}_pages", src, "")
        except PathError as exc:
            return fail(OP, str(exc), exc.hint, progress)
        directory.mkdir(parents=True, exist_ok=True)

        written = []
        for index in range(len(handle)):
            # pypdfium2's docstring says scale is a float; its annotation defaults
            # to int(1), so the stub is narrower than the documented API.
            image = handle[index].render(scale=dpi / 72.0).to_pil()  # type: ignore[arg-type]
            page_path = directory / f"page_{index + 1:04d}.png"
            image.save(page_path)
            finish(page_path)
            written.append(page_path.name)
    finally:
        handle.close()

    progress.append(ok_step(f"rendered {len(written)} page(s) at {dpi} DPI"))
    return ok(OP, {"out": str(directory), "to": "images", "dpi": dpi, "files": len(written)}, progress, basis="native")


def _refuse_reconstruction(src: Path, target: str, progress: list[dict]) -> dict:
    """Say no to pdf -> docx/xlsx/pptx, and say why.

    This is reconstruction from glyph coordinates, not conversion. The
    commercial sites use commercial engines and there is no CPU-only
    open-source path to their quality; the honest options are a GPL library
    whose licence this repo has not accepted, or a custom rebuild that would be
    visibly worse. Neither is shipped silently.

    The hint names what the caller can have instead, which is usually what they
    actually wanted: the tables as rows, or the text as markdown.
    """
    progress.append(info(f"pdf -> {target} is reconstruction, not conversion"))
    return fail(
        OP,
        f"Converting PDF to {target} is not supported on this build.",
        "A PDF stores glyphs at coordinates, not paragraphs, so this is a rebuild rather than a conversion "
        "and any CPU-only result would be visibly worse than a commercial converter. "
        "Use extract_tables() for the tables as rows, or convert(to='md') for the text.",
        progress,
        available=False,
    )


def _as_html(title: str, body: str) -> str:
    """Minimal, self-contained HTML. No stylesheet, no script, no CDN.

    A generated file must render with no sibling and no network -- an artifact
    that needs a companion file is an artifact that arrives broken.
    """
    import html as html_module

    escaped = html_module.escape(body)
    return (
        "<!doctype html>\n<html><head><meta charset='utf-8'>"
        f"<title>{html_module.escape(title)}</title></head>\n"
        f"<body><pre>{escaped}</pre></body></html>\n"
    )
