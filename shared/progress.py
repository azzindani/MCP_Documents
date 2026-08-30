"""Ring-1 pure utility — no I/O, no side effects. Safe innermost layer."""

from __future__ import annotations


def ok(message: str, detail: str = "") -> dict:
    entry: dict = {"status": "ok", "message": message}
    if detail:
        entry["detail"] = detail
    return entry


def fail(message: str, detail: str = "") -> dict:
    entry: dict = {"status": "fail", "message": message}
    if detail:
        entry["detail"] = detail
    return entry


def info(message: str, detail: str = "") -> dict:
    entry: dict = {"status": "info", "message": message}
    if detail:
        entry["detail"] = detail
    return entry


def warn(message: str, detail: str = "") -> dict:
    entry: dict = {"status": "warn", "message": message}
    if detail:
        entry["detail"] = detail
    return entry


def undo(message: str, detail: str = "") -> dict:
    entry: dict = {"status": "undo", "message": message}
    if detail:
        entry["detail"] = detail
    return entry
