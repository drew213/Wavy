"""Monthly series from the snapshot's step 38 aggregates and from stored orders, keyed by YYYY-MM."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from ..audit.snapshot import MissingData, Snapshot, parse_dt, ref

BURN_IN_DAYS = 90  # the first 90 days of the order window can't tell new buyers from returning ones


def monthly(snap: Snapshot, key: str, measurement: str = "count", window: str = "current",
            dimension: Optional[str] = None, full_only: bool = True) -> dict[str, float]:
    """{month: value} for one aggregate, full months only by default. MissingData when absent."""
    out = {}
    for b in snap.monthly(key, measurement, window, dimension):
        if b.value is not None and (b.full or not full_only):
            out[b.label] = b.value
    return out


def try_monthly(snap: Snapshot, key: str, measurement: str = "count", window: str = "current",
                dimension: Optional[str] = None, full_only: bool = True) -> Optional[dict[str, float]]:
    try:
        return monthly(snap, key, measurement, window, dimension, full_only)
    except MissingData:
        return None


def dimension_total(snap: Snapshot, key: str, measurement: str, window: str = "current") -> dict[str, float]:
    """{dimension value: total over the window} for a `by` aggregate."""
    out: dict[str, float] = defaultdict(float)
    for r in snap.rows(key, window):
        dim = (r.get("dimensions") or [""])[0] or ""
        out[dim] += sum(float(v or 0) for v in (r.get("measurements") or {}).get(measurement) or [])
    return dict(out)


def year_ago(label: str) -> str:
    y, m = label.split("-")
    return f"{int(y) - 1}-{m}"


def quarter(label: str) -> str:
    y, m = label.split("-")
    return f"{y}-Q{(int(m) - 1) // 3 + 1}"


def orders_rows(snap: Snapshot, days: Optional[int] = None) -> tuple[list[dict], datetime, Optional[datetime]]:
    """(deduplicated order rows with parsed times as `_t`, window end, window start)."""
    data = snap.load("orders")
    end = parse_dt(data.get("end")) or snap.as_of
    if end is None:
        raise MissingData("placed-orders-24mo.json has no end date", ref("orders", "end"))
    start = parse_dt(data.get("start"))
    seen: set = set()
    rows = []
    for r in data.get("orders") or []:
        t = parse_dt(r.get("t"))
        if t is None or (days is not None and t < end - timedelta(days=days)):
            continue
        key = r.get("o")
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        rows.append({**r, "_t": t})
    rows.sort(key=lambda r: r["_t"])
    return rows, end, start


def first_orders(rows: list[dict]) -> dict[str, dict]:
    """profile -> its first observed order row."""
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("p") and r["p"] not in out:
            out[r["p"]] = r
    return out
