# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────────────
# documents-mcp-server — production container. ONE process, both sub-servers.
#
# unified_server.py mounts docs-read at /read/mcp and docs-edit at /edit/mcp
# inside one Starlette app on one port, so pypdfium2, pdfplumber and pikepdf
# load once rather than twice. Each sub-server's own /health, /version and
# OAuth routes come along under its prefix. servers/*/server.py stay usable
# directly for a local stdio install.
#
# Build:  docker build -t documents-mcp-server:latest .
# Run:    docker run --rm -p 8850:8850 documents-mcp-server:latest
# ─────────────────────────────────────────────────────────────────────────────

ARG PYTHON_VERSION=3.14-slim

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS builder
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
# Deps first, source second: pyproject.toml + uv.lock change far less often
# than core/ and servers/, so the expensive layer stays cached across edits.
# --no-dev drops the PEP 735 `dev` group (pytest, ruff, pyright), which has no
# business in a runtime image.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY core ./core
COPY servers ./servers
COPY shared ./shared
COPY unified_server.py ./

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS runtime

# Ghostscript is NOT installed, and that is a licence decision rather than an
# oversight. It is AGPL-3.0, whose §13 reaches users who interact with a
# program over a network -- which is precisely what this deployment is. Calling
# it as a separate process is a defensible boundary, but shipping an image that
# bundles it makes the distribution question live, and this repo is permissive
# throughout by explicit policy (docs/TECH_STACK.md).
#
# The cost is real and is stated rather than hidden: optimize(action='compress')
# does object-stream and stream compression through QPDF, and does NOT
# downsample images, so a scanned document compresses little. core/binaries.py
# detects the absence and the response carries `note_images` saying so -- the
# tool never claims a compression it did not perform.
#
# An operator who accepts AGPL for their own deployment gets it with
# `--build-arg INSTALL_GHOSTSCRIPT=1`; no code changes, because binaries.py
# looks the executable up at call time.
ARG INSTALL_GHOSTSCRIPT=0

# libreoffice-writer/-calc/-impress back convert(to='pdf'), which shells out to
# `soffice --headless --convert-to pdf`. Scoped to the three component packages
# rather than the libreoffice metapackage, which would also drag in base and
# its java stack. All three are needed: TO_PDF_INPUTS in _edit_convert.py spans
# Writer formats (docx/doc/odt/rtf/html/txt/md), Calc's (xlsx/xls/ods/csv) and
# Impress's (pptx/ppt/odp), and a missing component makes soffice HANG on that
# input rather than fail -- which is why the tool carries a timeout.
#
# fonts-dejavu-core explicitly: LibreOffice with no font at all still exits 0
# and produces a PDF, so a missing font is not an error here, it is a page of
# blanks that every structural check passes.
#
# tesseract-ocr-eng matches ocr()'s `language` default. Other languages are one
# package each (tesseract-ocr-deu, ...), which is exactly what the tool's own
# failure hint tells the caller to install.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc libreoffice-impress \
        fonts-dejavu-core \
        tesseract-ocr tesseract-ocr-eng \
    && if [ "$INSTALL_GHOSTSCRIPT" = "1" ]; then \
        apt-get install -y --no-install-recommends ghostscript; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# uid/gid pinned to 999 rather than left to `-r`'s countdown. The shared
# e2e-smoke action and every compose bind-mount chown to 999:999, and the
# LibreOffice packages above create system users of their own -- so the first
# free system uid is not a constant across rebuilds. A root-owned mount makes
# every generated document fail EACCES while /health stays green.
RUN groupadd -r -g 999 app && useradd -r -u 999 -g app -m -d /home/app app

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/core /app/core
COPY --from=builder /app/servers /app/servers
COPY --from=builder /app/shared /app/shared
COPY pyproject.toml unified_server.py ./

# HOME is set explicitly because LibreOffice writes a user profile on first run
# and dies without a writable one. Docker reads HOME from /etc/passwd, but only
# when the image is run as this user by name -- `docker run --user 999:999`
# gets HOME=/, and soffice then fails with a javaldx/profile error that says
# nothing about permissions.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app \
    DOCS_HOST=0.0.0.0 \
    DOCS_PORT=8850

USER app
EXPOSE 8850

# Rendering and OCR hold the GIL or block on a subprocess for tens of seconds,
# and a health check should detect "dead", not "busy" -- the sibling Office
# deployment marked twelve healthy sub-servers unhealthy on a 3s probe during a
# 17s workbook write. Widened so a genuinely dead server is still caught inside
# ~2 minutes.
HEALTHCHECK --interval=30s --timeout=12s --start-period=15s --retries=4 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"DOCS_PORT\"]}/health', timeout=10)" || exit 1

ENTRYPOINT ["python", "unified_server.py"]
