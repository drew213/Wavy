"""Repeat purchase and subscription checks (RET-*). Order history is placed-orders-24mo.json,
deduplicated by order id, null-profile rows excluded and counted. "First observed" in a 24-month
window is not "truly new" (library note)."""
from __future__ import annotations

import re
from datetime import timedelta

from .checks import NotApplicable, as_dict, check, ev, outcome, pct
from .flows import ORDER_TRIGGER_NAMES, live_of
from .library import th, th_range
from .snapshot import MissingData, Snapshot, last_full, ref

# RET-01 logic: the cohort is profiles whose first observed order is 24 to 12 months before capture.
COHORT_MIN_AGE_DAYS = 365
COHORT_MAX_AGE_DAYS = 730
REPEAT_WINDOW_DAYS = 365

# RET-03/RET-04: subscription metric names -> event type. Names vary by platform (Loop, Recharge,
# WooCommerce Subscriptions), so these are matched by keyword; unmatched metrics are listed.
SUB_TYPES = (
    ("started", re.compile(r"start|creat|activat|new subscri", re.I)),
    ("cancelled", re.compile(r"cancel|status.?chang", re.I)),
    ("renewal", re.compile(r"renew|charge|recurr|billing", re.I)),
)
SUB_FLOW_TYPES = (
    ("upcoming", re.compile(r"upcoming|reminder|will renew|before.*(charge|renew)", re.I)),
    ("failed_payment", re.compile(r"fail|declin|payment.*(error|issue)|dunning", re.I)),
    ("cancelled", re.compile(r"cancel", re.I)),
    ("paused", re.compile(r"pause", re.I)),
)


@check("RET-01")
def first_buyers_return(snap: Snapshot, spec: dict):
    snap.require("orders")
    p = th(spec, "pass", r">= {n}% repeat", 0.01)
    f_lo = th(spec, "fail", r"< {n}%", 0.01)
    hist = snap.order_history()
    end = hist.end or snap.as_of
    if end is None:
        raise MissingData("placed-orders-24mo.json has no end date", ref("orders", "end"))
    lo = end - timedelta(days=COHORT_MAX_AGE_DAYS)
    if hist.start and hist.start > lo:
        lo = hist.start
    hi = end - timedelta(days=COHORT_MIN_AGE_DAYS)
    tz = snap.tz
    by_month: dict[str, list[int]] = {}
    cohort = repeat = 0
    for times in hist.by_profile.values():
        first = times[0]
        if not (lo <= first <= hi):
            continue
        cohort += 1
        again = len(times) >= 2 and (times[1] - first) <= timedelta(days=REPEAT_WINDOW_DAYS)
        repeat += again
        row = by_month.setdefault(first.astimezone(tz).strftime("%Y-%m"), [0, 0])
        row[0] += 1
        row[1] += again
    if not cohort:
        raise MissingData("no profile's first observed order has 12 months of follow-up in the order window", ref("orders", "orders"))
    rate = repeat / cohort
    value = as_dict({"cohort": cohort, "repeat_buyers": repeat, "repeat_rate": rate,
                     "cohort_window": [lo.date().isoformat(), hi.date().isoformat()],
                     "by_first_order_month": {m: {"buyers": n, "repeat_rate": r / n} for m, (n, r) in sorted(by_month.items())},
                     "null_profile_orders_excluded": hist.null_profile, "duplicate_orders_removed": hist.duplicates})
    evidence = [ev(ref("orders", "orders"), f"orders[].p,t for first buyers {lo.date()}..{hi.date()} (deduplicated by order id)")]
    msg = f"{pct(rate, 1)} of first-time buyers ordered again within 12 months"
    verdict = "pass" if rate >= p else ("fail" if rate < f_lo else "warn")
    return outcome(verdict, msg, value, evidence)


@check("RET-02")
def follow_up_timing(snap: Snapshot, spec: dict):
    """Pass: a live post-purchase/replenishment email lands between the 25th percentile and the
    median reorder gap. Warn: the closest email lands within 0.5-1.5x the median. Fail: no email
    lands in that wider window either (none at all, all too early, or all after 1.5x)."""
    snap.require("api", "orders")
    w_lo, w_hi = th_range(spec, "warn", r"within {n}-{n}x median")
    median, p25, n = snap.median_repeat_gap()
    if median is None:
        raise MissingData("no profile with two observed orders", ref("orders", "orders"))
    flows = [f for k in ("post_purchase", "replenishment") for f in live_of(snap, k)
             if (f.trigger_metric or "").strip().lower() in ORDER_TRIGGER_NAMES]
    offsets = [(f, s.offset_days) for f in flows for s in f.live_sends if s.channel == "email" and s.offset_days is not None]
    in_band = [(f, o) for f, o in offsets if p25 <= o <= median]
    near = [(f, o) for f, o in offsets if w_lo * median <= o <= w_hi * median]
    closest = min(offsets, key=lambda x: abs(x[1] - median)) if offsets else None
    value = as_dict({"median_gap_days": median, "p25_gap_days": p25, "repeat_buyers": n,
                     "email_offsets_days": sorted(round(o, 1) for _, o in offsets), "flows": [f.id for f in flows],
                     "closest_offset_days": closest[1] if closest else None, "product_level": False})
    evidence = [ev(ref("orders", "orders"), f"1st-to-2nd order gap: median {median:.0f} days, p25 {p25:.0f} days ({n} buyers)")]
    evidence += [ev(f.ref(), f"order-triggered flow {f.id} ({f.kind})") for f in flows[:5]]
    band = f"{p25:.0f}-{median:.0f} days"
    if in_band:
        return outcome("pass", f"a live post-purchase email lands at {in_band[0][1]:.0f} days, inside the {band} reorder band",
                       value, evidence)
    if near:
        best = min(near, key=lambda x: abs(x[1] - median))
        return outcome("warn", f"closest email lands at {best[1]:.0f} days, near but outside the {band} band", value, evidence)
    if not offsets:
        return outcome("fail", "no live order-triggered post-purchase or replenishment email", value, evidence)
    return outcome("fail", f"no email lands between {w_lo * median:.0f} and {w_hi * median:.0f} days after an order "
                           f"(median reorder gap {median:.0f} days)", value, evidence)


def _sub_metrics(snap: Snapshot) -> list[dict]:
    snap.require("totals")
    out = []
    for key, choice in (snap.at("totals", "metricChoices", default={}) or {}).items():
        if not key.startswith("subscription_") or not isinstance(choice, dict) or not choice.get("used"):
            continue
        name = choice["used"].get("name") or ""
        kind = next((k for k, rx in SUB_TYPES if rx.search(name)), None)
        try:
            months = last_full(snap.monthly(key, "count"), 2)
        except MissingData:
            months = []
        out.append({"key": key, "id": choice["used"].get("id"), "name": name, "type": kind,
                    "months": {b.label: b.value for b in months},
                    "total": sum(b.value or 0 for b in snap.monthly(key, "count")) if months else None})
    return out


def _subscription_state(snap: Snapshot) -> tuple[list[dict], bool]:
    """(subscription metrics, whether any carries events). Raises NotApplicable per the na_rule."""
    sells = snap.context.get("sells_subscriptions")
    if sells is False:
        raise NotApplicable("the client confirmed there's no subscription product")
    metrics = _sub_metrics(snap)
    active = any((m["total"] or 0) > 0 for m in metrics)
    if not active and sells is not True:
        raise NotApplicable("no subscription metric carries events in the window and the client hasn't confirmed a "
                            "subscription product; confirm before scoring")
    return metrics, active


@check("RET-03")
def subscription_events_flowing(snap: Snapshot, spec: dict):
    metrics, active = _subscription_state(snap)
    evidence = [ev(ref("totals", "aggregates", m["key"], "current"), f"subscription metric '{m['name']}'") for m in metrics[:10]] \
        or [ev(ref("totals", "metricChoices"), "no subscription metrics selected by step 38")]
    if not active:
        return outcome("fail", "subscriptions are sold but no subscription metric has events in the window",
                       {"metrics": metrics}, evidence)
    by_type: dict[str, bool] = {}
    for kind, _ in SUB_TYPES:
        ms = [m for m in metrics if m["type"] == kind]
        by_type[kind] = any(len(m["months"]) == 2 and all((v or 0) > 0 for v in m["months"].values()) for m in ms)
    last2_any = any(any((v or 0) > 0 for v in m["months"].values()) for m in metrics)
    value = as_dict({"metrics": metrics, "types_with_events_each_month": by_type})
    if not last2_any:
        return outcome("fail", "no subscription metric has events in the last 2 full months", value, evidence)
    missing = [k for k, ok in by_type.items() if not ok]
    if not missing:
        return outcome("pass", "start, cancel and renewal events arrive every month", value, evidence)
    return outcome("warn", f"no monthly events for: {', '.join(missing)}", value, evidence)


@check("RET-04")
def subscription_flows_live(snap: Snapshot, spec: dict):
    metrics, _ = _subscription_state(snap)
    snap.require("api")
    sub_ids = {str(m["id"]) for m in metrics}
    native = set(snap.context.get("subscription_native_messages") or [])
    covered: dict[str, list[str]] = {}
    flows = [f for f in snap.flows() if f.classified_live and str(f.trigger_id) in sub_ids]
    for f in flows:
        name = f.trigger_metric or ""
        for kind, rx in SUB_FLOW_TYPES:
            if rx.search(name) or rx.search(f.name):
                covered.setdefault(kind, []).append(f.id)
                break
    for kind in native:
        covered.setdefault(kind, []).append("native (client-confirmed)")
    value = {"covered": covered, "subscription_flows": [f.id for f in flows], "native_confirmed": sorted(native)}
    evidence = [ev(f.ref("triggers", 0), f"live flow on subscription metric '{f.trigger_metric}'") for f in flows[:10]] \
        or [ev(ref("api", "flows", "details"), "no live flow triggers on a subscription metric")]
    have = [k for k in ("cancelled", "upcoming") if k in covered]
    if len(have) == 2:
        return outcome("pass", "cancellation and upcoming-renewal messages are covered", value, evidence)
    if have:
        return outcome("warn", f"only {have[0]} is covered (cancellation and upcoming renewal both need a flow or "
                               "confirmed native message)", value, evidence)
    return outcome("fail", "no live flow (or confirmed native message) for cancellation or upcoming renewal", value, evidence)
