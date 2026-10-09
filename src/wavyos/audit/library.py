"""The Audit OS check library: loading it, selecting checks for a track, and reading thresholds.

The library (check-library.json, copied from audit-os/checks/) is the spec. Thresholds are read
from each check's `thresholds` text with an explicit pattern per number, so a changed number in
the library changes the check. A pattern that no longer matches raises LibraryError, which the
engine records as an `error` verdict instead of silently scoring with a stale value.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

LIBRARY_PATH = Path(__file__).with_name("check-library.json")
TRACKS = ("snapshot", "deep")


class LibraryError(Exception):
    """The library text doesn't say what a check expects it to say."""


@lru_cache(maxsize=4)
def load_library(path: str | None = None) -> dict[str, Any]:
    with open(path or LIBRARY_PATH, encoding="utf-8") as fh:
        lib = json.load(fh)
    ids = [c["id"] for c in lib["checks"]]
    if len(ids) != len(set(ids)):
        raise LibraryError("duplicate check ids in the library")
    return lib


def specs(library: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    lib = library or load_library()
    return {c["id"]: c for c in lib["checks"]}


def spec(check_id: str) -> dict[str, Any]:
    return specs()[check_id]


def applies(spec_: dict[str, Any], track: str) -> bool:
    return track in (spec_.get("tracks") or [])


_NUM = r"(\d+(?:,\d{3})*(?:\.\d+)?)"


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def th(spec_: dict[str, Any], band: str, pattern: str, scale: float = 1.0, group: int = 1) -> float:
    """Read one number from spec['thresholds'][band] with a regex.

    `{n}` in the pattern stands for a number (commas and decimals allowed). scale converts
    units, e.g. 0.01 for percentages. Example: th(spec, "pass", r">= {n}%", 0.01) -> 0.35.
    """
    text = (spec_.get("thresholds") or {}).get(band)
    if not isinstance(text, str):
        raise LibraryError(f"{spec_.get('id')}: no '{band}' threshold text")
    rx = pattern.replace("{n}", _NUM)
    found = re.search(rx, text)
    if not found:
        raise LibraryError(f"{spec_.get('id')}: '{band}' threshold {text!r} doesn't match {pattern!r}")
    return _to_float(found.group(group)) * scale


def th_range(spec_: dict[str, Any], band: str, pattern: str, scale: float = 1.0) -> tuple[float, float]:
    """Read a 'low-high' pair, e.g. th_range(spec, 'warn', r'{n}-{n}%', 0.01) -> (0.2, 0.35)."""
    text = (spec_.get("thresholds") or {}).get(band) or ""
    rx = pattern.replace("{n}", _NUM)
    found = re.search(rx, text)
    if not found:
        raise LibraryError(f"{spec_.get('id')}: '{band}' threshold {text!r} doesn't match {pattern!r}")
    return _to_float(found.group(1)) * scale, _to_float(found.group(2)) * scale
