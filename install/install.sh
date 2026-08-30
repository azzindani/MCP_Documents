#!/bin/sh
# documents-mcp-server installer — Linux / macOS
# POSIX sh compatible (no bash-isms)

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==================================="
echo "  documents-mcp-server installer"
echo "==================================="
echo ""

# ─── 1. Python ────────────────────────────────────────────────────────────────
# 3.14 exactly, not "3.11 or newer". pyproject pins `requires-python = "==3.14.*"`
# and uv.lock is resolved against it, so a 3.13 interpreter fails at sync with a
# message about the lock rather than about the version.

check_python() {
    if command -v python3 > /dev/null 2>&1; then
        PYTHON_CMD=python3
    elif command -v python > /dev/null 2>&1; then
        PYTHON_CMD=python
    else
        echo "Error: Python not found."
        echo "uv can install 3.14 for you — see the next step — or get it from"
        echo "https://www.python.org/downloads/"
        PYTHON_CMD=""
        return 0
    fi
    echo "✔ Python $($PYTHON_CMD --version 2>&1 | cut -d' ' -f2) found"
}

# ─── 2. uv ────────────────────────────────────────────────────────────────────

check_uv() {
    if command -v uv > /dev/null 2>&1; then
        echo "✔ uv found: $(uv --version)"
    else
        echo "→ uv not found. Installing..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        if command -v uv > /dev/null 2>&1; then
            echo "✔ uv installed: $(uv --version)"
            echo "  Note: add ~/.local/bin to your PATH for future sessions."
        else
            echo "Error: uv installation failed."
            echo "Install manually: curl -LsSf https://astral.sh/uv/install.sh | sh"
            exit 1
        fi
    fi
}

# ─── 3. Dependencies ──────────────────────────────────────────────────────────
# Plain `uv sync`, not `--all-packages`: the siblings are uv workspaces with a
# package per sub-server, this is one project with two server modules.

install_deps() {
    echo ""
    echo "→ Installing Python dependencies (uv will fetch 3.14 if needed)..."
    cd "$REPO_DIR"
    uv python install 3.14 > /dev/null 2>&1 || true
    uv sync
    echo "✔ Dependencies installed"
}

# ─── 4. Optional external binaries ────────────────────────────────────────────
# Reported, never installed. Both are large, both need root, and both have a
# tool that already refuses by name when they are missing — a caller can act on
# "tesseract is not installed"; nobody can act on errno 2.

check_binaries() {
    echo ""
    echo "→ Optional external tools:"
    if command -v soffice > /dev/null 2>&1 || command -v libreoffice > /dev/null 2>&1; then
        echo "  ✔ LibreOffice  — convert(to='pdf') will work"
    else
        echo "  ✗ LibreOffice  — convert(to='pdf') will refuse by name."
        echo "                   apt-get install libreoffice-writer libreoffice-calc libreoffice-impress"
    fi
    if command -v tesseract > /dev/null 2>&1; then
        echo "  ✔ Tesseract    — ocr() will work"
    else
        echo "  ✗ Tesseract    — ocr() will refuse by name."
        echo "                   apt-get install tesseract-ocr tesseract-ocr-eng"
    fi
    echo "  Everything else — all 7 read tools and the rest of docs-edit — is pure Python."
}

# ─── 5. Platform ──────────────────────────────────────────────────────────────

select_platform() {
    echo ""
    echo "Which AI platform do you use?"
    echo "  1) LM Studio (recommended for local LLMs)"
    echo "  2) Claude Desktop"
    echo "  3) Cursor"
    echo "  4) Windsurf"
    echo "  5) Cline (VS Code)"
    echo "  6) All of them"
    echo ""
    printf "Enter number [1]: "
    read PLATFORM_CHOICE

    case "$PLATFORM_CHOICE" in
        2) PLATFORM="claude-desktop" ;;
        3) PLATFORM="cursor" ;;
        4) PLATFORM="windsurf" ;;
        5) PLATFORM="cline" ;;
        6) PLATFORM="all" ;;
        *) PLATFORM="lmstudio" ;;
    esac
    echo "→ Selected platform: $PLATFORM"
}

# ─── 6. Servers ───────────────────────────────────────────────────────────────

select_servers() {
    echo ""
    echo "Which servers do you want to register?"
    echo "  1) docs-read  — probe, outline, find, extract, extract_tables, read_page, to_markdown"
    echo "  2) docs-edit  — assemble, convert, optimize, ocr, protect, redact"
    echo "  3) Both"
    echo ""
    printf "Enter number [3]: "
    read SERVER_CHOICE

    case "$SERVER_CHOICE" in
        1) SERVERS="docs_read" ;;
        2) SERVERS="docs_edit" ;;
        *) SERVERS="all" ;;
    esac
    echo "→ Selected servers: $SERVERS"
}

# ─── 7. Constrained mode ──────────────────────────────────────────────────────

select_constrained() {
    echo ""
    printf "Enable constrained mode? (tighter budgets for 8 GB machines) [y/N]: "
    read CONSTRAINED_CHOICE
    case "$CONSTRAINED_CHOICE" in
        [Yy]*) CONSTRAINED="--constrained" ;;
        *) CONSTRAINED="" ;;
    esac
}

# ─── 8. Write config ──────────────────────────────────────────────────────────

write_config() {
    echo ""
    echo "→ Registering servers in the $PLATFORM config..."
    cd "$REPO_DIR"
    uv run python install/mcp_config_writer.py \
        --servers "$SERVERS" \
        --platform "$PLATFORM" \
        $CONSTRAINED
}

# ─── Main ─────────────────────────────────────────────────────────────────────

check_python
check_uv
install_deps
check_binaries
select_platform
select_servers
select_constrained
write_config

echo ""
echo "==================================="
echo "  Installation complete!"
echo "==================================="
echo ""
echo "Restart your AI application to load the new MCP tools."
echo ""
echo "For help or issues: https://github.com/azzindani/MCP_Documents/issues"
