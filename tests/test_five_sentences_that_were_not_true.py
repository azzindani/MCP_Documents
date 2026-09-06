"""Five descriptions promised something the tool does not do.

An MCP client shows one sentence per tool and nothing else, so that sentence is
the entire contract. Round 24 checked each against the live server and five in
this repo failed:

    find      "Returns page locations, not content"  -- every hit has a snippet
    read_page "text, tables, links, and how ..."     -- there is no links field
    ocr       "Page range required."                 -- it works without one
    optimize  "... or linearise a PDF"               -- the action is linearize
    protect   "... or clear a PDF's permission ..."  -- the action is permissions

The code is right in all five. `find`'s snippets are how a caller decides which
page to extract; `ocr` defaulting to the pages that lack a text layer is better
than demanding a range; `optimize` and `protect` refuse a wrong action with a
hint naming the real one. Only the sentences were wrong -- and in the last two,
the word the description used was the word the tool rejected, which is the
worst version of this because the description is where a caller looks first.

`read_page` is the one that lost a promise rather than gaining a correction:
there is no links extractor and the design's path is probe -> find -> extract,
so the honest fix is to stop advertising one.

These tests read the description out of the source and check it against what
the tool actually returns and actually accepts, so a future edit to either side
has to move both.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _docstring(relpath: str, func: str) -> str:
    tree = ast.parse((REPO / relpath).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"{func} not found in {relpath}")


SERVER_READ = "servers/docs_read/server.py"
SERVER_EDIT = "servers/docs_edit/server.py"


class TestTheDescriptionNoLongerPromisesWhatIsMissing:
    def test_read_page_does_not_advertise_links(self):
        """There is no links extractor; the path is probe -> find -> extract."""
        doc = _docstring(SERVER_READ, "read_page")
        assert "links" not in doc.lower()

    def test_read_page_still_names_what_it_does_return(self):
        doc = _docstring(SERVER_READ, "read_page").lower()
        assert "text" in doc and "tables" in doc

    def test_find_no_longer_claims_it_returns_no_content(self):
        """It returns a snippet per hit, which is the point of it."""
        doc = _docstring(SERVER_READ, "find").lower()
        assert "not content" not in doc
        assert "snippet" in doc

    def test_find_still_promises_it_will_not_return_a_whole_page(self):
        """The real contract: locations and fragments, never the page itself."""
        doc = _docstring(SERVER_READ, "find").lower()
        assert "never a page" in doc or "not a page" in doc

    def test_ocr_no_longer_says_a_page_range_is_required(self):
        doc = _docstring(SERVER_EDIT, "ocr").lower()
        assert "required" not in doc

    def test_ocr_says_what_it_does_instead(self):
        doc = _docstring(SERVER_EDIT, "ocr").lower()
        assert "lack" in doc or "default" in doc


class TestTheDescriptionUsesTheWordTheParserTakes:
    """The two that documented a verb their own argument parser rejects."""

    @pytest.mark.parametrize("action", ["compress", "repair", "linearize"])
    def test_optimize_documents_each_real_action(self, action):
        assert action in _docstring(SERVER_EDIT, "optimize")

    def test_optimize_no_longer_documents_the_spelling_it_rejects(self):
        assert "linearise" not in _docstring(SERVER_EDIT, "optimize")

    @pytest.mark.parametrize("action", ["encrypt", "decrypt", "permissions"])
    def test_protect_documents_each_real_action(self, action):
        assert action in _docstring(SERVER_EDIT, "protect")

    def test_protect_no_longer_says_clear(self):
        assert "clear" not in _docstring(SERVER_EDIT, "protect")

    def test_protect_still_says_a_password_is_needed(self):
        assert "password" in _docstring(SERVER_EDIT, "protect").lower()


class TestTheDocumentedActionsAreTheAcceptedOnes:
    """Read both sides: the sentence, and the vocabulary the code enforces."""

    def test_every_action_optimize_documents_is_one_it_accepts(self):
        from servers.docs_edit._edit_optimize import optimize

        doc = _docstring(SERVER_EDIT, "optimize")
        documented = [a for a in ("compress", "repair", "linearize") if a in doc]
        assert documented
        for action in documented:
            out = optimize(source="/nonexistent.pdf", action=action)
            # The action is accepted; it fails later, on the missing file.
            assert "is not an action" not in str(out.get("error", "")), action

    def test_every_action_protect_documents_is_one_it_accepts(self):
        from servers.docs_edit._edit_secure import protect

        doc = _docstring(SERVER_EDIT, "protect")
        documented = [a for a in ("encrypt", "decrypt", "permissions") if a in doc]
        assert documented
        for action in documented:
            out = protect(source="/nonexistent.pdf", action=action, password="x")
            assert "is not an action" not in str(out.get("error", "")), action

    def test_a_word_the_description_does_not_use_is_still_refused(self):
        """The refusals were always well-formed; only the sentence was wrong."""
        from servers.docs_edit._edit_optimize import optimize

        out = optimize(source="/nonexistent.pdf", action="linearise")
        assert "is not an action" in str(out.get("error", ""))
        assert "linearize" in str(out.get("hint", ""))
