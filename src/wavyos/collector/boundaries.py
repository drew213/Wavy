"""Account-local calendar boundaries as UTC instants, DST-safe. Port of aggregate-boundaries.js.

Klaviyo's aggregate `timezone` sets the buckets, not the datetime filter, so a month that should start
at local midnight has to be sent as the matching UTC instant. A local midnight that doesn't exist, or
exists twice, is an error: the caller must pass an explicit instant instead.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_EXPLICIT = re.compile(r"T.*(?:Z|[+-]\d{2}:\d{2})$", re.I)
_MIDNIGHT = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:T00:00:00(?:\.0{1,3})?)?$")


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def boundary_to_utc(value: str | datetime, tz_name: str) -> str:
    """A calendar date (or date at T00:00:00) in tz_name -> the UTC instant of that local midnight."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetimes are ambiguous; pass a calendar date string or an aware datetime")
        return _iso_z(value)
    if not isinstance(value, str):
        raise ValueError("a boundary is a calendar midnight or an explicit timestamp")
    if _EXPLICIT.search(value):
        return _iso_z(datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00")))
    match = _MIDNIGHT.match(value)
    if not match:
        raise ValueError("naive timestamps must be calendar midnight; keep Z on real UTC timestamps")
    day = date.fromisoformat(match.group(1))
    tz = ZoneInfo(tz_name)
    anchor = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    offsets = {(anchor + timedelta(days=d)).astimezone(tz).utcoffset() for d in (-2, -1, 0, 1, 2)}
    found = []
    for off in offsets:
        instant = anchor - off
        local = instant.astimezone(tz)
        if (local.date(), local.hour, local.minute, local.second) == (day, 0, 0, 0):
            found.append(instant)
    if len(found) != 1:
        raise ValueError("local midnight is missing or ambiguous; supply an explicit timestamp")
    return _iso_z(found[0])


def window_to_utc(window: dict, tz_name: str) -> dict:
    """{start, end} boundaries -> {start, end} UTC instants. End must follow start."""
    start, end = boundary_to_utc(window["start"], tz_name), boundary_to_utc(window["end"], tz_name)
    if datetime.fromisoformat(start.replace("Z", "+00:00")) >= datetime.fromisoformat(end.replace("Z", "+00:00")):
        raise ValueError("a window's end must follow its start")
    return {"start": start, "end": end}


def month_start(now: datetime, tz_name: str, offset_months: int) -> str:
    """The first of the month `offset_months` from now's month, in the account's calendar, as YYYY-MM-DD."""
    local = now.astimezone(ZoneInfo(tz_name))
    total = local.year * 12 + (local.month - 1) + offset_months
    return f"{total // 12:04d}-{total % 12 + 1:02d}-01"
