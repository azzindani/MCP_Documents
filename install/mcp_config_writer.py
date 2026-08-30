#!/usr/bin/env python3
"""MCP config writer for documents-mcp-server.

Registers `docs-read` and `docs-edit` in an AI platform's config file.

    python install/mcp_config_writer.py --servers all --platform lmstudio
    python install/mcp_config_writer.py --servers docs_read --platform claude-desktop

Deliberately self-contained. The sibling repos' writers import
`shared/file_utils.py` and the client-path helpers out of
`shared/platform_utils.py`, and this repo has neither: `file_utils.py` carries
a vocabulary specific to the data server, and `platform_utils.py` is one of the
two shared files that is per-repo by design (CLAUDE.md §13 rule 13). Copying
either one in to serve an installer would fork a file the fleet keeps
byte-identical, to gain about forty lines.

The entries also differ from the siblings' by necessity. Office and the rest
are uv workspaces whose sub-servers are installed packages with console
scripts, so their entry is `uv run --directory <server> docx-basic`. This repo
is ONE project with two server modules, so the entry runs the module by path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()

# Mount name -> the server module that serves it. Plural, and matching the HTTP
# mounts in unified_server.py, so one deployment cannot be called two things.
SERVERS = {
    "docs_read": ("docs-read", "servers/docs_read/server.py"),
    "docs_edit": ("docs-edit", "servers/docs_edit/server.py"),
}


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA", "~")).expanduser()


def lmstudio_config_path() -> Path:
    if _is_windows():
        return _appdata() / "LM Studio" / "mcp.json"
    if _is_macos():
        return Path.home() / "Library" / "Application Support" / "LM Studio" / "mcp.json"
    return Path.home() / ".config" / "LM Studio" / "mcp.json"


def claude_desktop_config_path() -> Path:
    if _is_windows():
        return _appdata() / "Claude" / "claude_desktop_config.json"
    if _is_macos():
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def cursor_config_path() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def windsurf_config_path() -> Path:
    return Path.home() / ".codeium" / "windsurf" / "mcp_config.json"


def cline_config_path() -> Path:
    if _is_windows():
        return _appdata() / "Code" / "User" / "settings.json"
    if _is_macos():
        return Path.home() / "Library" / "Application Support" / "Code" / "User" / "settings.json"
    return Path.home() / ".config" / "Code" / "User" / "settings.json"


PLATFORMS = {
    "lmstudio": lmstudio_config_path,
    "claude-desktop": claude_desktop_config_path,
    "cursor": cursor_config_path,
    "windsurf": windsurf_config_path,
    "cline": cline_config_path,
}


def read_config(path: Path) -> dict:
    """Parse a config file, or return {} when there is nothing there yet.

    A file that exists but is not valid JSON is an ERROR, not an empty config:
    treating it as empty would overwrite whatever the user had, including
    servers this installer never put there.
    """
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def write_config(path: Path, data: dict) -> None:
    """Write atomically: temp file in the same directory, then replace.

    A config truncated half-way through a write is a client that will not
    start, and this runs against files the user did not create.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def server_entry(module_path: str, env: dict) -> dict:
    return {
        "command": "uv",
        "args": ["run", "--directory", str(REPO_ROOT), "python", str(REPO_ROOT / module_path)],
        "env": env,
    }


def register(config_path: Path, key: str, chosen: list[str], env: dict) -> tuple[list[str], list[str]]:
    data = read_config(config_path)
    data.setdefault(key, {})
    added, already = [], []
    for name in chosen:
        mount, module_path = SERVERS[name]
        if mount in data[key]:
            already.append(mount)
            continue
        data[key][mount] = server_entry(module_path, env)
        added.append(mount)
    write_config(config_path, data)
    return added, already


def main() -> None:
    parser = argparse.ArgumentParser(description="Register docs-read / docs-edit in an AI platform's MCP config.")
    parser.add_argument("--servers", default="all", help=f"Comma-separated: {', '.join(SERVERS)} — or 'all'.")
    parser.add_argument("--platform", default="lmstudio", choices=[*PLATFORMS, "all"])
    parser.add_argument(
        "--constrained",
        action="store_true",
        help="Set MCP_CONSTRAINED_MODE=1 — tighter budgets for 8 GB machines.",
    )
    parser.add_argument("--config", default="", help="Override the config file path.")
    args = parser.parse_args()

    if args.servers.lower() == "all":
        chosen = list(SERVERS)
    else:
        chosen = [s.strip() for s in args.servers.split(",") if s.strip()]
        unknown = [s for s in chosen if s not in SERVERS]
        if unknown:
            print(f"Error: unknown server(s): {unknown}. Available: {list(SERVERS)}")
            sys.exit(1)

    env = {"MCP_CONSTRAINED_MODE": "1"} if args.constrained else {}
    targets = list(PLATFORMS) if args.platform == "all" else [args.platform]

    for platform in targets:
        path = Path(args.config) if args.config else PLATFORMS[platform]()
        if not args.config and not path.parent.exists():
            print(f"  [{platform}] skipped — not installed here ({path.parent} does not exist)")
            continue
        key = "cline.mcpServers" if platform == "cline" else "mcpServers"
        try:
            added, already = register(path, key, chosen, env)
        except Exception as exc:  # noqa: BLE001 - one bad config must not stop the rest
            print(f"  [{platform}] error: {exc}")
            continue
        if added:
            print(f"  [{platform}] registered: {', '.join(added)}")
            print(f"             config: {path}")
        if already:
            print(f"  [{platform}] already present, left alone: {', '.join(already)}")

    print("\nDone. Restart your AI application to load the new tools.")


if __name__ == "__main__":
    main()
