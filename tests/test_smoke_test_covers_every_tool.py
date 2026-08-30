"""remote_smoke_test.sh must exercise every tool this repo defines.

The smoke test is the only thing that calls these tools the way a client does:
over HTTP, through bearer auth, against a running container, with LibreOffice
and Tesseract actually present. pytest deliberately never does (CLAUDE.md §12),
and the CI matrix runners have neither binary -- so a tool missing from that
script is a tool nothing end-to-end has ever run.

Nothing used to check the two stayed in step, and that is not hypothetical: a
coverage sweep driven through a harness was told to call "every tool the server
exposes (list them first, then call each)" for two of the sibling servers, and
it listed some and called none -- 19 tools silently unexercised, with the run
still reporting a clean pass because it only reported on what it had chosen to
call. The same drift happens here the moment someone adds a tool and forgets
the script.

This runs offline: the tool list comes from the AST of the server modules, not
from a running server, so it works in CI with no network and no container.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_TEST = REPO_ROOT / "remote_smoke_test.sh"

# Tools defined here but not mounted on the deployed server, so the smoke test
# has nothing to call them against. Keep this empty unless a tier is genuinely
# not deployed, and say why -- it is the one way to hide a tool from this check.
NOT_DEPLOYED: frozenset[str] = frozenset()

_SKIP_DIRS = {".venv", "node_modules", ".git", "tests", "build", "dist"}


def _server_files() -> list[Path]:
    return [p for p in REPO_ROOT.rglob("server.py") if not _SKIP_DIRS & set(p.parts)]


def _defined_tools() -> set[str]:
    """Names of every @mcp.tool()-decorated function in the repo."""
    names: set[str] = set()
    for path in _server_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute) and target.attr == "tool":
                    names.add(node.name)
    return names


def _smoke_test_tokens() -> set[str]:
    """Every identifier-shaped token in the smoke test.

    Deliberately not a parse of the tool calls: each repo's script drives them
    through its own shell helper (`run <tier> <tool> <args>`, inline JSON, and
    so on), and a regex per repo is a regex that rots. A tool name appearing
    nowhere in the file is unambiguous; that is what this catches.
    """
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", SMOKE_TEST.read_text(encoding="utf-8")))


class TestEveryToolIsExercised:
    def test_the_smoke_test_exists(self):
        assert SMOKE_TEST.is_file(), f"{SMOKE_TEST.name} is the only end-to-end coverage this repo has"

    def test_some_tools_were_found(self):
        """A broken enumerator would make every other test here pass vacuously."""
        assert len(_defined_tools()) > 0

    def test_the_tool_count_is_the_documented_ceiling(self):
        """13 tools across 2 sub-servers is a hard ceiling (CLAUDE.md §3).

        Here rather than in a doc because a ceiling nothing enforces is a
        preference. A 14th tool is a decision, and this is where it gets made.
        """
        assert len(_defined_tools()) == 13

    def test_every_defined_tool_appears_in_the_smoke_test(self):
        missing = sorted(_defined_tools() - _smoke_test_tokens() - NOT_DEPLOYED)
        assert not missing, (
            f"{len(missing)} tool(s) defined but never exercised end-to-end: {missing}. "
            f"Add them to {SMOKE_TEST.name}, or to NOT_DEPLOYED here with a reason."
        )

    @pytest.mark.parametrize("name", sorted(NOT_DEPLOYED))
    def test_exempt_tools_are_still_defined(self, name):
        """An exemption for a tool that no longer exists is stale bookkeeping."""
        assert name in _defined_tools()


class TestTheEnvelopePatternsAreEscaped:
    """The smoke test's extractors must read ESCAPED JSON.

    A tool's document arrives as the JSON *string* `result.content[0].text`, so
    on the wire it is `\\"pages\\": 3`, not `"pages": 3`. A pattern written for
    unescaped JSON matches nothing while every call still succeeds -- which is
    how four of the six sibling repos silently stopped asserting anything after
    the official-SDK migration dropped `structuredContent`. Nothing in the
    script's own output distinguishes "extracted an empty string" from "the
    server returned nothing", so this is checked here instead.
    """

    def _extractor_lines(self) -> list[str]:
        text = SMOKE_TEST.read_text(encoding="utf-8")
        return [line for line in text.splitlines() if "grep -oE" in line or "grep -Eq" in line]

    def test_there_are_extractors_to_check(self):
        assert self._extractor_lines()

    def test_every_key_pattern_allows_the_backslash(self):
        bad = [
            line.strip()
            for line in self._extractor_lines()
            # A key pattern is a quoted name followed by a colon. Escaped-JSON
            # aware ones spell it `\\?"key\\?"`; the broken form is a bare
            # `"key"` with no optional backslash in front of either quote.
            if re.search(r'(?<!\\)\\"[A-Za-z_$][A-Za-z0-9_{}$]*\\"[^:]*:', line) and "\\\\?" not in line
        ]
        assert not bad, "extractor(s) written for unescaped JSON, so they match nothing on the wire: " + str(bad)

    def test_every_extractor_ends_with_a_failure_guard(self):
        """A grep that matches nothing must not abort the script.

        Under `set -o pipefail` a non-matching grep makes the pipeline
        non-zero, and inside `VAR=$(...)` that aborts before the `|| fail` that
        was meant to report it -- the run dies mid-way looking like a hang
        rather than a failed assertion.
        """
        text = SMOKE_TEST.read_text(encoding="utf-8")
        for name in ("extract()", "extract_num()"):
            body_start = text.index(name.rstrip("()") + "() {")
            body = text[body_start : text.index("\n}", body_start)]
            assert "|| true" in body, f"{name} can abort the script when it matches nothing"
