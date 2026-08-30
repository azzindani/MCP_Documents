@echo off
setlocal enabledelayedexpansion

echo ===================================
echo   documents-mcp-server installer
echo ===================================
echo.

set REPO_DIR=%~dp0..
cd /d "%REPO_DIR%"

:: --- 1. Check Python ---------------------------------------------------------
:: uv fetches 3.14 itself below, so a missing interpreter is not fatal here.

set PYTHON_CMD=
for %%P in (py python3 python) do (
    if "!PYTHON_CMD!"=="" (
        where %%P >nul 2>&1
        if not errorlevel 1 (
            set PYTHON_CMD=%%P
        )
    )
)

if "!PYTHON_CMD!"=="" (
    echo [--] Python not found on PATH. uv will install 3.14 in the next step.
) else (
    for /f "tokens=2" %%V in ('!PYTHON_CMD! --version 2^>^&1') do set PYTHON_VERSION=%%V
    echo [OK] Python !PYTHON_VERSION! found
)

:: --- 2. Enable long paths ----------------------------------------------------
:: A repo of documents plus a uv venv comfortably exceeds MAX_PATH on Windows.

reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f >nul 2>&1
echo [OK] Long path support enabled

:: --- 3. Check / install uv ---------------------------------------------------

where uv >nul 2>&1
if errorlevel 1 (
    echo [->] uv not found. Installing...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set PATH=%USERPROFILE%\.local\bin;%PATH%
    where uv >nul 2>&1
    if errorlevel 1 (
        echo Error: uv installation failed.
        echo Install manually: https://docs.astral.sh/uv/getting-started/installation/
        pause
        exit /b 1
    )
    echo [OK] uv installed
) else (
    for /f "tokens=*" %%V in ('uv --version') do echo [OK] uv found: %%V
)

:: --- 4. Install dependencies -------------------------------------------------
:: Plain `uv sync`: this repo is ONE project with two server modules, not a uv
:: workspace with a package per sub-server like the siblings.

echo.
echo [->] Installing Python dependencies...
uv python install 3.14 >nul 2>&1
uv sync
if errorlevel 1 (
    echo Error: dependency installation failed.
    pause
    exit /b 1
)
echo [OK] Dependencies installed

:: --- 5. Optional external tools ----------------------------------------------
:: Reported, never installed. Both tools that need them refuse by name when
:: they are absent, which is something a caller can act on.

echo.
echo [->] Optional external tools:
where soffice >nul 2>&1
if errorlevel 1 (
    echo   [--] LibreOffice  -- convert^(to='pdf'^) will refuse by name.
    echo                        https://www.libreoffice.org/download/
) else (
    echo   [OK] LibreOffice  -- convert^(to='pdf'^) will work
)
where tesseract >nul 2>&1
if errorlevel 1 (
    echo   [--] Tesseract    -- ocr^(^) will refuse by name.
    echo                        https://github.com/UB-Mannheim/tesseract/wiki
) else (
    echo   [OK] Tesseract    -- ocr^(^) will work
)
echo   Everything else is pure Python.

:: --- 6. Platform selection ---------------------------------------------------

echo.
echo Which AI platform do you use?
echo   1^) LM Studio ^(recommended for local LLMs^)
echo   2^) Claude Desktop
echo   3^) Cursor
echo   4^) Windsurf
echo   5^) Cline ^(VS Code^)
echo   6^) All of them
echo.
set /p PLATFORM_CHOICE="Enter number [1]: "

if "!PLATFORM_CHOICE!"=="2" (set PLATFORM=claude-desktop) else (
if "!PLATFORM_CHOICE!"=="3" (set PLATFORM=cursor) else (
if "!PLATFORM_CHOICE!"=="4" (set PLATFORM=windsurf) else (
if "!PLATFORM_CHOICE!"=="5" (set PLATFORM=cline) else (
if "!PLATFORM_CHOICE!"=="6" (set PLATFORM=all) else (
    set PLATFORM=lmstudio
)))))

echo [->] Selected platform: !PLATFORM!

:: --- 7. Server selection -----------------------------------------------------

echo.
echo Which servers do you want to register?
echo   1^) docs-read  -- probe, outline, find, extract, extract_tables, read_page, to_markdown
echo   2^) docs-edit  -- assemble, convert, optimize, ocr, protect, redact
echo   3^) Both
echo.
set /p SERVER_CHOICE="Enter number [3]: "

if "!SERVER_CHOICE!"=="1" (set SERVERS=docs_read) else (
if "!SERVER_CHOICE!"=="2" (set SERVERS=docs_edit) else (
    set SERVERS=all
))

echo [->] Selected servers: !SERVERS!

:: --- 8. Constrained mode -----------------------------------------------------

echo.
set /p CONSTRAINED_CHOICE="Enable constrained mode? (tighter budgets for 8 GB machines) (y/N): "
if /i "!CONSTRAINED_CHOICE!"=="y" (
    set CONSTRAINED=--constrained
) else (
    set CONSTRAINED=
)

:: --- 9. Write config ---------------------------------------------------------

echo.
echo [->] Registering servers in the !PLATFORM! config...
uv run python install\mcp_config_writer.py --servers !SERVERS! --platform !PLATFORM! !CONSTRAINED!

echo.
echo ===================================
echo   Installation complete!
echo ===================================
echo.
echo Restart your AI application to load the new MCP tools.
echo.
echo For help: https://github.com/azzindani/MCP_Documents/issues
echo.
pause
