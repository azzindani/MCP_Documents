"""Running a caller's regular expression without betting the process on it.

`find(regex=True)` compiles a pattern the caller wrote and runs it over every
page. Python's `re` has no timeout and no step limit, so one pattern with
nested quantifiers -- `(\\s*\\w+)+$` is the classic -- backtracks for longer
than the universe on a single page of ordinary prose. Measured on the BBCA
filing: killed at 120 seconds, still running, on ONE page.

That is not a slow answer, it is a worker that never comes back. On a deployed
HTTP service the caller's client times out and the server keeps burning a core
until someone restarts the container, and nothing in the response, the log or
the health check says why.

So the match runs in a child process that can be killed, and the parent holds a
deadline. Three measurements decided the shape:

  * The worst LEGITIMATE pattern over all 183 pages of the filing -- including
    a two-named-group parser and a leading `.*` -- takes **0.324s**. Ten
    patterns were timed; nine were under 0.04s. The default ceiling of 10s is
    thirty times the worst real case, which is the headroom a guard needs if it
    is not to break the work it protects.
  * The child costs **tens of milliseconds**, against the seconds `find`
    already spends reading a 183-page document. Python 3.14 starts children
    with `forkserver` on Linux, which is safe to use from a server's worker
    thread -- plain `fork` inherits locks other threads hold -- and pickling
    the whole document's text costs 2.1ms for 725 KB.
  * A terminated child stops the backtracking dead: alive after 3s, SIGTERM,
    exitcode -15, parent unharmed.

Only `regex=True` pays for this. A literal query is `re.escape`d and cannot
backtrack, so it stays in-process exactly as before.
"""

from __future__ import annotations

import multiprocessing as mp
import re

from core import budget

# One entry per match: which text it was in, where it started and ended, and
# any NAMED groups. Deliberately not the `re.Match` -- that cannot cross a
# process boundary, and the caller only ever needs these four things.
Hit = tuple[int, int, int, dict[str, str]]


class ScanTimeout(Exception):
    """The pattern did not finish inside its deadline."""

    def __init__(self, seconds: float):
        super().__init__(f"the pattern did not finish within {seconds:g}s")
        self.seconds = seconds


def _count(pattern: re.Pattern[str], texts: list[str], ceiling: int) -> tuple[int, list[Hit], list[int]]:
    """Every match, the first `ceiling` of them, and which texts they fell in.

    Three separate answers, and `where` is one of them rather than something to
    derive from `hits` afterwards. `hits` stops at the ceiling and `where` does
    not: the tool calling this returns page locations as its primary answer, so
    a `pages` string that shrank when `max_hits` was lowered would make the
    tool's own point unreliable. Counting all three in the one pass is also the
    only way to do it inside a child process, which cannot be asked follow-up
    questions.
    """
    total = 0
    hits: list[Hit] = []
    where: list[int] = []
    for index, text in enumerate(texts):
        found = False
        for match in pattern.finditer(text):
            total += 1
            found = True
            if len(hits) < ceiling:
                start, end = match.span()
                groups = {k: v for k, v in (match.groupdict() or {}).items() if v is not None}
                hits.append((index, start, end, groups))
        if found:
            where.append(index)
    return total, hits, where


def _scan(source: str, flags: int, texts: list[str], ceiling: int, conn) -> None:
    """Run in the child. Sends the counts and exits."""
    conn.send(_count(re.compile(source, flags), texts, ceiling))
    conn.close()


def finditer_bounded(
    source: str,
    flags: int,
    texts: list[str],
    ceiling: int,
    timeout: float | None = None,
) -> tuple[int, list[Hit], list[int]]:
    """Every match of `source` across `texts`, or ScanTimeout.

    `ceiling` bounds how many matches are carried back; the total is counted
    regardless, because a truncated list that cannot say how much it truncated
    is the failure this repo keeps finding.
    """
    seconds = budget.regex_seconds() if timeout is None else timeout
    # forkserver on Linux, spawn on Windows and macOS: both pickle the args, so
    # neither inherits a lock held by another thread of this server. Named
    # explicitly rather than left to the default, because the default has
    # changed once already (3.14 moved Linux off `fork`) and a guard that
    # depends on it silently becomes unsafe.
    # Written as a branch on two literals rather than one computed argument:
    # `get_context` is overloaded per method name, and passing an expression
    # collapses it to the base class, which does not declare `.Process`.
    if "forkserver" in mp.get_all_start_methods():
        ctx = mp.get_context("forkserver")
    else:
        ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_scan, args=(source, flags, texts, ceiling, child), daemon=True)
    process.start()
    child.close()  # only the child writes; the parent's copy must go or poll() never ends
    try:
        # Read BEFORE joining. A child that has filled the pipe buffer is
        # blocked in write() and will never exit, so joining first would wait
        # for the deadline on every large result rather than on a runaway one.
        if not parent.poll(seconds):
            raise ScanTimeout(seconds)
        answer: tuple[int, list[Hit], list[int]] = parent.recv()
    except EOFError as exc:  # the child died without sending: a crash, not a timeout
        raise ScanTimeout(seconds) from exc
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
    return answer


def matches_of(
    texts: list[str], source: str, flags: int, ceiling: int, guarded: bool
) -> tuple[int, list[Hit], list[int]]:
    """The same answer either way; `guarded` chooses whether a child pays for it.

    A literal query has been through `re.escape`, so it holds no quantifier at
    all and cannot backtrack. Paying for a process to protect against that
    would slow the common path to guard against nothing.
    """
    if guarded:
        return finditer_bounded(source, flags, texts, ceiling)
    return _count(re.compile(source, flags), texts, ceiling)
