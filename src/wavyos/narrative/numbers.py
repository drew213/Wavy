"""Numbers as they appear in prose: "$10.0k", "4,210", "-33%", "2.4 pp", "1.2M".

A token remembers how precisely it was written, so "$10.0k" matches a fact of 10,012 (shown to the
nearest 100) and "3.1%" matches 0.0312 but not 0.0350.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator, Optional

MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
# Text that holds digits but no measured claim: calendar dates, times of day and ordinal labels.
NOT_CLAIMS = re.compile("|".join([
    r"\b\d{4}-\d{2}(?:-\d{2})?(?:T[\d:.+Z-]+)?\b",                # ISO dates and months
    rf"\b{MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:\s*[-–]\s*\d{{1,2}})?(?:,?\s+\d{{4}})?\b",  # Nov 27, Nov 15-30, 2025
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTHS}\b",                  # 27 Nov
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",                           # 11/27
    r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b",                         # 10am, 9:30 pm
    r"\b(?:weeks?|wk|days?|steps?|phases?|q|tier|rank|top|round|touch|version|v|stage)\s*#?\d{1,3}"
    r"(?:\s*[-–]\s*\d{1,3})?\b(?![.,]\d|\s?%|\s?pp\b)",            # Week 3, Q4, top 5, weeks 1-2
    r"#\d{1,3}\b",
    r"(?<![$€£\d,.])\b(?:19|20)\d{2}\b(?![.,]\d|\s?%)",            # years
]), re.I)
# Standard reporting windows and horizons: the window a number is measured over, not a claim.
WINDOW = re.compile(r"\b(?P<n>\d{1,3})[- ]?(?P<u>days?|weeks?|months?|hours?|hrs?)\b", re.I)
WINDOWS = {"day": {1, 7, 14, 28, 30, 45, 60, 90, 180, 365}, "week": {1, 4, 8, 12, 13, 26, 52},
           "month": {1, 3, 6, 12, 18, 24}, "hour": {24, 48, 72}, "hr": {24, 48, 72}}
NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>[-+−])?(?P<cur>[$€£])?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"(?P<suf>\s?(?:k|m|bn|b)\b)?(?P<pct>\s?(?:%|pp\b|percent\b|points?\b))?(?P<x>x\b)?", re.I)
SCALE = {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9}


@dataclass(frozen=True)
class Token:
    raw: str
    value: float          # as written, scaled by any k/M suffix (percent tokens stay in percent units)
    step: float           # the place value of the last digit written
    pct: bool
    currency: bool
    multiple: bool


def _window_unit(u: str) -> str:
    u = u.lower().rstrip("s")
    return "hr" if u == "hr" else u


def masked(text: str, *, allow_windows: bool = True) -> str:
    """Text with dates, ordinals and standard windows blanked out."""
    text = NOT_CLAIMS.sub(" ", text)
    if allow_windows:
        text = WINDOW.sub(lambda mo: " " if int(mo["n"]) in WINDOWS.get(_window_unit(mo["u"]), ()) else mo[0], text)
    return text


def tokens(text: str, *, allow_windows: bool = True) -> Iterator[Token]:
    for mo in NUMBER.finditer(masked(text or "", allow_windows=allow_windows)):
        digits = mo["int"].replace(",", "")
        dec = mo["dec"] or ""
        value = float(digits + dec)
        step = 10 ** -(len(dec) - 1) if dec else 1.0
        suf = (mo["suf"] or "").strip().lower()
        if suf:
            value, step = value * SCALE[suf], step * SCALE[suf]
        if mo["sign"] in ("-", "−"):
            value = -value
        yield Token(mo[0].strip(), value, step, bool(mo["pct"]), bool(mo["cur"]), bool(mo["x"]))


def as_number(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def matches(tok: Token, value: float) -> bool:
    """Could tok be value as written (rounded to tok's precision; ratios may be written as percents)?"""
    candidates = [abs(value)]
    if tok.pct or abs(value) <= 1.5:
        candidates.append(abs(value) * 100)
    x = abs(tok.value)
    for c in candidates:
        if abs(x - c) <= max(tok.step / 2 + 1e-9, 0.005 * c):
            return True
    return False
