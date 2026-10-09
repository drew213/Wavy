"""A saved Gen 1 collector output folder (klaviyo-audit/output/<slug>/), read lazily and read-only.

Checks ask the snapshot for a file or a JSON path. Anything that isn't there raises MissingData,
which the engine turns into an `na` verdict with the reason: absent data is never a measured zero.

Files (see klaviyo-audit/README.md):
  run.json                         runner log: which steps ran and whether they succeeded
  account-audit-api.json           step 24: account, flows with definitions, campaigns, forms, lists, segments, metrics
  performance-cross-review.json    step 34: flow and campaign message performance, 90 and 365 days
  live-template-weight-audit.json  step 37: live flow template sizes (Gmail clipping)
  account-totals.json              step 38: monthly aggregates (current and prior windows), campaigns, forms report
  placed-orders-24mo.json          step 38: one compact row per order, pseudonymous profile id only
  form-details.json, q4-baseline.json   steps 31 and 35 (deep track)
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FILES: dict[str, str] = {
    "run": "run.json",
    "api": "account-audit-api.json",
    "perf": "performance-cross-review.json",
    "templates": "live-template-weight-audit.json",
    "totals": "account-totals.json",
    "orders": "placed-orders-24mo.json",
    "forms": "form-details.json",
    "q4": "q4-baseline.json",
}
# conventions.tracks: snapshot = steps 24, 34, 37, 38 (plus the runner log).
TRACK_FILES: dict[str, tuple[str, ...]] = {
    "snapshot": ("run", "api", "perf", "templates", "totals", "orders"),
    "deep": ("run", "api", "perf", "templates", "totals", "orders", "forms", "q4"),
}
_BY_FILENAME = {v: k for k, v in FILES.items()}
_MISSING = object()


class MissingData(Exception):
    """A file or JSON path a check needs isn't in the snapshot."""

    def __init__(self, reason: str, ref: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.ref = ref


def _key(name: str) -> str:
    if name in FILES:
        return name
    if name in _BY_FILENAME:
        return _BY_FILENAME[name]
    raise KeyError(f"unknown snapshot file {name!r}")


def pointer(*path: Any) -> str:
    """RFC 6901 JSON pointer for a path of keys and indexes."""
    if not path:
        return ""
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in path)


def ref(name: str, *path: Any) -> str:
    return f"{FILES[_key(name)]}#{pointer(*path)}"


def parse_dt(value: Any, tz: Optional[ZoneInfo] = None) -> Optional[datetime]:
    """ISO timestamp -> aware datetime. Naive values are taken to be in tz (UTC if none)."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz or timezone.utc)
    return dt


def add_month(dt: datetime) -> datetime:
    """The same wall-clock time one calendar month later, in dt's own zone (DST-aware)."""
    y, mo = (dt.year + 1, 1) if dt.month == 12 else (dt.year, dt.month + 1)
    return dt.replace(year=y, month=mo, day=1)


@dataclass
class Bucket:
    """One monthly bucket of a step 38 aggregate."""
    index: int
    label: str                 # YYYY-MM in the account's timezone
    start: datetime            # local, aware
    end: datetime              # local, aware
    full: bool                 # the whole month lies inside the window
    value: Optional[float]     # None: not reported


@dataclass
class OrderHistory:
    """placed-orders-24mo.json, deduplicated by order id, grouped by pseudonymous profile."""
    by_profile: dict[str, list[datetime]]
    orders: int                # after dedupe, null-profile rows included
    duplicates: int
    null_profile: int
    with_items: list[tuple[datetime, bool]] = field(default_factory=list)  # (time, has line items) per deduped order
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    truncated: Optional[bool] = None
    error: Any = None

    def repeat_gaps_days(self) -> list[float]:
        """Days between each profile's 1st and 2nd observed order."""
        out = []
        for times in self.by_profile.values():
            if len(times) >= 2:
                out.append((times[1] - times[0]).total_seconds() / 86400)
        return out


def quantile(values: list[float], q: float) -> Optional[float]:
    """Linear-interpolated quantile (q in 0..1); None for an empty list."""
    if not values:
        return None
    v = sorted(values)
    if len(v) == 1:
        return v[0]
    pos = (len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


class Snapshot:
    """Lazy, read-only view of one collector output folder (or of in-memory dicts, for tests).

    context carries human-confirmed facts the collectors can't know, e.g.
    {"store_platform": "Shopify", "sells_subscriptions": True}. Checks that need one and don't
    get it say so instead of assuming.
    """

    def __init__(self, folder: str | Path | None = None, *, files: Optional[dict[str, Any]] = None,
                 context: Optional[dict[str, Any]] = None):
        self.folder = Path(folder) if folder is not None else None
        if self.folder is not None and not self.folder.is_dir():
            raise FileNotFoundError(f"snapshot folder not found: {self.folder}")
        self._cache: dict[str, Any] = {}
        self._mem = {_key(k): v for k, v in (files or {}).items()}
        self.context: dict[str, Any] = dict(context or {})
        self._orders: Optional[OrderHistory] = None
        self._flows = None

    @classmethod
    def from_dicts(cls, files: dict[str, Any], context: Optional[dict[str, Any]] = None) -> "Snapshot":
        return cls(None, files=files, context=context)

    # ------------------------------------------------------------ files

    @property
    def ref_label(self) -> str:
        return str(self.folder) if self.folder is not None else "memory"

    def has(self, name: str) -> bool:
        k = _key(name)
        if k in self._mem or k in self._cache:
            return True
        return self.folder is not None and (self.folder / FILES[k]).is_file()

    def load(self, name: str) -> Any:
        k = _key(name)
        if k in self._cache:
            return self._cache[k]
        if k in self._mem:
            self._cache[k] = self._mem[k]
            return self._cache[k]
        if self.folder is None or not (self.folder / FILES[k]).is_file():
            raise MissingData(f"{FILES[k]} is not in the snapshot", FILES[k])
        with open(self.folder / FILES[k], encoding="utf-8") as fh:
            self._cache[k] = json.load(fh)
        return self._cache[k]

    def get(self, name: str) -> Any:
        try:
            return self.load(name)
        except MissingData:
            return None

    def require(self, *names: str) -> None:
        absent = [FILES[_key(n)] for n in names if not self.has(n)]
        if absent:
            raise MissingData(f"needs {', '.join(absent)}, which the snapshot doesn't have", absent[0])

    def missing(self, track: str = "snapshot") -> list[str]:
        return [FILES[k] for k in TRACK_FILES[track] if not self.has(k)]

    def present(self) -> list[str]:
        return [FILES[k] for k in FILES if self.has(k)]

    def at(self, name: str, *path: Any, default: Any = _MISSING) -> Any:
        """Walk a JSON path; MissingData (or default) if any step is absent."""
        node = self.load(name)
        for i, p in enumerate(path):
            ok = False
            if isinstance(node, dict) and p in node:
                node, ok = node[p], True
            elif isinstance(node, list) and isinstance(p, int) and -len(node) <= p < len(node):
                node, ok = node[p], True
            if not ok:
                if default is not _MISSING:
                    return default
                raise MissingData(f"{ref(name, *path[: i + 1])} is missing", ref(name, *path[: i + 1]))
        return node

    # ------------------------------------------------------------ account facts

    @property
    def timezone_name(self) -> Optional[str]:
        for name, path in (("totals", ("account", "timezone")), ("api", ("account", "items", 0, "attributes", "timezone")),
                           ("run", ("account", "timezone"))):
            if self.has(name):
                tz = self.at(name, *path, default=None)
                if tz:
                    return tz
        return None

    @property
    def tz(self) -> ZoneInfo:
        name = self.timezone_name
        try:
            return ZoneInfo(name) if name else ZoneInfo("UTC")
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    @property
    def captured(self) -> dict[str, datetime]:
        out = {}
        for name in ("api", "perf", "templates", "totals"):
            if self.has(name):
                dt = parse_dt(self.at(name, "capturedAt", default=None))
                if dt:
                    out[FILES[name]] = dt
        return out

    @property
    def as_of(self) -> Optional[datetime]:
        """When the data was captured: step 38's capturedAt (it defines the reporting windows),
        else the earliest capturedAt among the other step files, else the runner's last run."""
        cap = self.captured
        if FILES["totals"] in cap:
            return cap[FILES["totals"]]
        if cap:
            return min(cap.values())
        if self.has("run"):
            hist = self.at("run", "history", default=[]) or []
            if hist:
                return parse_dt(hist[-1].get("ranAt"))
        return None

    # ------------------------------------------------------------ step 38 monthly aggregates

    def aggregate(self, key: str) -> dict:
        agg = self.at("totals", "aggregates", key)
        if not isinstance(agg, dict) or agg.get("notFound") or "current" not in agg:
            raise MissingData(f"step 38 has no {key} aggregate (metric not found in the account)",
                              ref("totals", "aggregates", key))
        return agg

    def monthly(self, key: str, measurement: str = "count", window: str = "current",
                dimension: Optional[str] = None) -> list[Bucket]:
        """Monthly buckets for one aggregate. `full` marks whole months inside the window, so a
        partial capture month or a pre-window bucket is never compared with a full one."""
        agg = self.aggregate(key)
        w = agg.get(window)
        if not isinstance(w, dict):
            raise MissingData(f"step 38 {key} has no {window} window", ref("totals", "aggregates", key, window))
        tz = self.tz
        dates = [parse_dt(d) for d in w.get("dates") or []]
        if not dates or any(d is None for d in dates):
            raise MissingData(f"step 38 {key}.{window} has no usable dates", ref("totals", "aggregates", key, window, "dates"))
        win = w.get("window") or self.at("totals", "windows", window, default={}) or {}
        w_start, w_end = parse_dt(win.get("start"), tz), parse_dt(win.get("end"), tz)
        values: list[Optional[float]] = [None] * len(dates)
        rows = w.get("rows") or []
        row = None
        if dimension is None:
            row = rows[0] if rows else None
        else:
            row = next((r for r in rows if (r.get("dimensions") or [None])[0] == dimension), None)
        if row is not None:
            series = (row.get("measurements") or {}).get(measurement)
            if series is None:
                raise MissingData(f"step 38 {key}.{window} has no '{measurement}' measurement",
                                  ref("totals", "aggregates", key, window, "rows", rows.index(row), "measurements"))
            if len(series) != len(dates):
                raise MissingData(f"step 38 {key}.{window} '{measurement}' doesn't line up with its dates",
                                  ref("totals", "aggregates", key, window, "rows", rows.index(row), "measurements", measurement))
            values = [None if v is None else float(v) for v in series]
        out = []
        local = [d.astimezone(tz) for d in dates]
        for i, start in enumerate(local):
            end = local[i + 1] if i + 1 < len(local) else add_month(start)
            full = (w_start is None or start >= w_start) and (w_end is None or end <= w_end)
            out.append(Bucket(i, start.strftime("%Y-%m"), start, end, full, values[i]))
        return out

    def rows(self, key: str, window: str = "current") -> list[dict]:
        agg = self.aggregate(key)
        return (agg.get(window) or {}).get("rows") or []

    def metric_choice(self, key: str) -> Optional[dict]:
        """step 38 metricChoices.<key>.used, or None when step 38 found no such metric."""
        return self.at("totals", "metricChoices", key, "used", default=None)

    # ------------------------------------------------------------ step 34 reports

    def report_results(self, report: str) -> list[dict]:
        rep = self.at("perf", "reports", report)
        if not rep.get("ok", False):
            raise MissingData(f"step 34 report {report} failed", ref("perf", "reports", report))
        res = (((rep.get("value") or {}).get("data") or {}).get("attributes") or {}).get("results")
        if res is None:
            raise MissingData(f"step 34 report {report} has no results", ref("perf", "reports", report, "value"))
        return res

    # ------------------------------------------------------------ orders

    def order_history(self) -> OrderHistory:
        if self._orders is not None:
            return self._orders
        data = self.load("orders")
        rows = data.get("orders")
        if rows is None:
            raise MissingData("placed-orders-24mo.json has no orders array", ref("orders", "orders"))
        seen: set = set()
        by_profile: dict[str, list[datetime]] = {}
        dupes = nulls = 0
        items: list[tuple[datetime, bool]] = []
        for r in rows:
            oid = r.get("o")
            if oid is not None:
                if oid in seen:
                    dupes += 1
                    continue
                seen.add(oid)
            t = parse_dt(r.get("t"))
            if t is None:
                continue
            items.append((t, bool(r.get("i"))))
            p = r.get("p")
            if not p:
                nulls += 1
                continue
            by_profile.setdefault(p, []).append(t)
        for times in by_profile.values():
            times.sort()
        self._orders = OrderHistory(by_profile=by_profile, orders=len(items), duplicates=dupes, null_profile=nulls,
                                    with_items=items, start=parse_dt(data.get("start")), end=parse_dt(data.get("end")),
                                    truncated=data.get("truncated"), error=data.get("error"))
        return self._orders

    def median_repeat_gap(self) -> tuple[Optional[float], Optional[float], int]:
        """(median, 25th percentile, n) days between 1st and 2nd observed order (RET-02, FLW-06)."""
        gaps = self.order_history().repeat_gaps_days()
        return (statistics.median(gaps) if gaps else None), quantile(gaps, 0.25), len(gaps)

    # ------------------------------------------------------------ flows

    def flows(self):
        if self._flows is None:
            from .flows import classify_flows
            self._flows = classify_flows(self)
        return self._flows


def full_buckets(buckets: Iterable[Bucket]) -> list[Bucket]:
    return [b for b in buckets if b.full]


def last_full(buckets: Iterable[Bucket], n: int) -> list[Bucket]:
    return full_buckets(buckets)[-n:]
