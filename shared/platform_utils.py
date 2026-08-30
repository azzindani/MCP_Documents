from __future__ import annotations

# Ring-2 infrastructure utility — reads environment at call time (not import
# time) so that MCP_CONSTRAINED_MODE changes after startup are honoured and
# test monkeypatching works without module reloads.
import os


def _constrained() -> bool:
    return os.environ.get("MCP_CONSTRAINED_MODE", "0") == "1"


def get_max_rows() -> int:
    return 20 if _constrained() else 100


def get_max_columns() -> int:
    return 20 if _constrained() else 50


def get_max_results() -> int:
    return 10 if _constrained() else 50


def get_max_lag() -> int:
    """Largest lag a lag-correlation sweep may test, in periods.

    The response carries one row per lag from -max_lag to +max_lag, so the cap
    bounds the answer at 2n+1 rows. It also bounds the multiple-comparison
    problem: every extra lag is another correlation tested against the same
    data, and the widest sweep is the one most likely to turn up a peak that is
    only noise.
    """
    return 10 if _constrained() else 30
