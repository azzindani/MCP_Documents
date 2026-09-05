"""Every response that reports a count reports it the same way.

`shared/counts.py` derives `truncated` from `returned` and `total` rather than
accepting one, so the flag cannot disagree with the numbers printed beside it.
The static rule below is repeated per repo on purpose: it reads *this* repo's
source on *this* repo's CI runner, where the sibling repos do not exist.

This repo had one emitter and it was the asymmetric kind. `find` set
`result["truncated"] = True` inside an `if hits > len(matches):` branch, so a
complete answer carried no flag at all. That reads as a smaller problem than it
is: a caller cannot tell "nothing was cut" from "this tool does not report
cutting", and only one of those means the answer is whole. An existing test
asserted the absence -- `assert "truncated" not in payload` -- which is how the
convention had become load-bearing; it now asserts `is False`, and the field it
was actually written to pin (`returned_limit`, the ceiling) is unchanged and
still appears only when a cap bit.

`find` was otherwise the best-behaved emitter in the fleet: `hits` was already
exact even when the returned list was not, so a caller could tell "47 hits,
here are 47" from "4,000 hits, narrow it down". That is the property the
contract generalises.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVERS = ROOT / "servers"
SHARED = ROOT / "shared"

_HAND_WRITTEN = re.compile(r'"truncated"\s*:')
_EXEMPT = "counts-contract: composite"
_LOOKBACK = 15


def _py_files() -> list[Path]:
    files = [p for p in SERVERS.rglob("*.py") if "__pycache__" not in p.parts]
    files += [p for p in SHARED.rglob("*.py") if "__pycache__" not in p.parts]
    return [p for p in files if p.name != "counts.py"]


def test_no_module_writes_the_truncated_key_by_hand():
    offenders: list[str] = []
    for path in _py_files():
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("#"):
                continue  # modules quote the banned string while explaining it
            if not _HAND_WRITTEN.search(line):
                continue
            if _EXEMPT in "\n".join(lines[max(0, i - _LOOKBACK) : i]):
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{i + 1}: {line.strip()}")
    assert not offenders, (
        "these write `truncated` by hand instead of calling counted():\n  "
        + "\n  ".join(offenders)
        + "\n\ncounted(returned, total) derives it, so the flag cannot disagree "
        "with the numbers printed beside it."
    )


def test_no_module_sets_truncated_only_when_it_is_true():
    """The asymmetric form: present when cut, absent when not."""
    pattern = re.compile(r'\[\s*"truncated"\s*\]\s*=')
    offenders: list[str] = []
    for path in _py_files():
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        "these assign `truncated` conditionally:\n  "
        + "\n  ".join(offenders)
        + "\n\nMerge counted(returned, total) into the response unconditionally. "
        "An absent flag and a False one make different claims, and a caller "
        "cannot tell a complete answer from a tool that stays quiet."
    )


def test_the_count_helper_is_importable_here():
    from shared.counts import count_violations, counted

    assert callable(counted) and callable(count_violations)
    assert counted(3, 3)["truncated"] is False
    assert counted(3, 9)["truncated"] is True
