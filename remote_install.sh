#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# MCP_Documents — remote bootstrap (Google Colab / any fresh Linux VM, no
# Docker needed). Installs uv, clones or updates this repo, and syncs the
# Python dependencies for both sub-servers.
#
# Companion to remote_launch.sh, and the same shape as the six siblings' — the
# one difference is `uv sync` rather than `uv sync --all-packages`, because
# this repo is ONE project with two server modules rather than a uv workspace
# with a package per sub-server.
#
# LibreOffice and Tesseract are installed here when apt is available, because
# unlike a laptop install a throwaway VM has nothing and `convert(to='pdf')`
# and `ocr()` are two of the thirteen tools. Both are optional: every other
# tool works without them, and those two refuse by name rather than failing
# inside a subprocess.
#
# Usage:
#   REPO_DIR=/content/MCP_Documents ./remote_install.sh
#   SKIP_BINARIES=1 ./remote_install.sh      # Python only, ~400 MB lighter
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_DIR="${REPO_DIR:-/content/MCP_Documents}"
REPO_URL="${REPO_URL:-https://github.com/azzindani/MCP_Documents.git}"

if ! command -v uv &>/dev/null; then
  echo "[remote_install] installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="${HOME}/.local/bin:${PATH}"

if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR" && git pull -q && echo "Updated: $(git log -1 --oneline)"
else
  git clone -q "$REPO_URL" "$REPO_DIR"
  echo "Cloned: $(cd "$REPO_DIR" && git log -1 --oneline)"
fi

cd "$REPO_DIR"
uv python install 3.14 >/dev/null 2>&1 || true
uv sync 2>&1 | tail -5

if [ "${SKIP_BINARIES:-0}" != "1" ] && command -v apt-get &>/dev/null; then
  echo "[remote_install] installing LibreOffice + Tesseract (optional, ~400 MB)..."
  # The component packages, not the libreoffice metapackage, which drags in
  # base and its java stack. All three are needed: Writer, Calc and Impress
  # each own part of what convert(to='pdf') accepts, and a missing component
  # makes soffice HANG on that input rather than fail.
  ${SUDO:-sudo} apt-get update -qq
  ${SUDO:-sudo} apt-get install -y -qq --no-install-recommends \
    libreoffice-writer libreoffice-calc libreoffice-impress \
    fonts-dejavu-core tesseract-ocr tesseract-ocr-eng >/dev/null
fi

echo ""
command -v soffice   &>/dev/null && echo "  ✔ LibreOffice — convert(to='pdf') available" \
                                 || echo "  ✗ LibreOffice — convert(to='pdf') will refuse by name"
command -v tesseract &>/dev/null && echo "  ✔ Tesseract   — ocr() available" \
                                 || echo "  ✗ Tesseract   — ocr() will refuse by name"
echo ""
echo "✓ MCP_Documents installed (docs-read: 7 tools, docs-edit: 6 tools)"
