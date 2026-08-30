"""Keep non-JSON floats out of a response, so a strict client can read it.

`Infinity`, `-Infinity` and `NaN` are not JSON. Python's encoder writes them as
bare tokens by extension and Python's decoder accepts them again, so the whole
problem is invisible from inside Python -- and every test here is Python.

Found by round 17's axis, which hands one artifact to a second server:

    apply_patch(..., column_math: clicks/impressions*100)   # impressions can be 0
    read_column_stats(ctr_pct)
      -> "mean": Infinity, "std": NaN, "max": Infinity

    python json.loads(strict)  REJECTED  non-JSON literal 'Infinity'
    node   JSON.parse          REJECTED  Unexpected token 'I' ... is not valid JSON

The damage is not the field, it is the payload: a JavaScript, Go or Rust client
cannot parse ANY of the response, so a division by zero in one column takes out
the entire reply. data-statistics reports the same quantities as `null` and
parses fine, which is how the disagreement surfaced -- the two servers were
asked about one file and answered in two formats, one of them unreadable.

shared/small_sample.py already had `finite()` for exactly this, docstring and
all. It was applied to one module (data_medium/_med_inspect.py) and nowhere
else, so every other tool kept emitting bare tokens. That is why this is a
choke point at the response boundary rather than a hunt through call sites: a
per-site fix is what was tried, and it stopped at the first sibling.

None is the right replacement rather than a string or a sentinel number.
"mean": null says the mean could not be computed; "mean": "Infinity" invites a
client to print it, and 1e308 invites arithmetic on it.
"""

from __future__ import annotations

import math
from typing import Any


def json_safe(value: Any) -> Any:
    """Return `value` with every non-finite float replaced by None.

    Walks dicts, lists and tuples. Everything else is returned unchanged --
    numpy scalars included, since `math.isfinite` accepts anything that
    converts to float and numpy floats do.
    """
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    # bool is an int subclass and always finite; checking float first keeps
    # numpy scalars (which are not `float` instances) on the isfinite path.
    if isinstance(value, bool) or isinstance(value, int):
        return value
    try:
        number = float(value)
    except TypeError, ValueError:
        return value
    if isinstance(value, str):
        return value  # "NaN" as text is the caller's data, not a float
    return value if math.isfinite(number) else None


def sanitize_responses(mcp: Any) -> None:
    """Strip non-JSON floats from every tool this server has registered.

    Install this BEFORE measure_responses(): wrappers nest, so the one applied
    first runs innermost, and the token estimate should describe the response
    that actually goes on the wire rather than the one before sanitising.
    """
    for tool in mcp._tool_manager._tools.values():
        fn = getattr(tool, "fn", None)
        if fn is None or getattr(fn, "__json_safe_wrapped__", False):
            continue
        tool.fn = _sanitised(fn)


def _sanitised(fn: Any) -> Any:
    import functools

    @functools.wraps(fn)
    def sanitised(*a: Any, **kw: Any) -> Any:
        return json_safe(fn(*a, **kw))

    sanitised.__json_safe_wrapped__ = True  # type: ignore[attr-defined]
    return sanitised
