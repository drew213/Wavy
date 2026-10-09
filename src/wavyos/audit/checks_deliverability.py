"""Deliverability checks (DLV-*). Monthly rates use full months only (Snapshot.monthly)."""
from __future__ import annotations

from .checks import as_dict, check, ev, outcome, pct, ratio, worst
from .library import th, th_range
from .snapshot import MissingData, Snapshot, full_buckets, ref

# DLV-03 logic: share of 90-day campaigns over the per-campaign unsubscribe limit.
UNSUB_CAMPAIGN_SHARE_PASS = 0.10   # pass if < 10% of campaigns exceed it
UNSUB_CAMPAIGN_SHARE_WARN = 0.25   # warn if 10-25% exceed it
# DLV-04 logic: providers with >= 2% of received volume over the last 3 full months; Apple is
# excluded from the comparison base (Mail Privacy Protection auto-opens). "Other" is Klaviyo's
# catch-all bucket, not a provider, so it's excluded too.
INBOX_MIN_SHARE = 0.02
INBOX_MONTHS = 3
INBOX_EXCLUDE_PREFIXES = ("apple",)
INBOX_EXCLUDE_NAMES = ("other",)


def _months(snap: Snapshot, key: str, measurement: str = "count", dimension: str | None = None) -> dict[str, float | None]:
    return {b.label: b.value for b in full_buckets(snap.monthly(key, measurement, dimension=dimension))}


def _series_ref(key: str, measurement: str = "count") -> str:
    return ref("totals", "aggregates", key, "current", "rows", 0, "measurements", measurement)


def _campaign_rates(snap: Snapshot, stat: str) -> list[tuple[str, float]]:
    out = []
    for r in snap.report_results("campaign90"):
        st = r.get("statistics") or {}
        if (st.get("delivered") or st.get("recipients") or 0) and st.get(stat) is not None:
            out.append((str((r.get("groupings") or {}).get("campaign_id")), float(st[stat])))
    return out


@check("DLV-01")
def spam_rate_low(snap: Snapshot, spec: dict):
    snap.require("totals", "perf")
    ok_max = th(spec, "pass", r"< {n}%", 0.01)
    camp_max = th(spec, "warn", r"a campaign >= {n}%", 0.01)
    fail_at = th(spec, "fail", r">= {n}%", 0.01)
    spam, recv = _months(snap, "marked_spam"), _months(snap, "received_email")
    rates = {m: spam[m] / recv[m] for m in recv if recv[m] and spam.get(m) is not None}
    if not rates:
        raise MissingData("no full month with both spam complaints and received email", _series_ref("received_email"))
    camps = _campaign_rates(snap, "spam_complaint_rate")
    hot = [c for c, r in camps if r >= camp_max]
    worst_m = max(rates, key=rates.get)
    value = as_dict({"monthly": rates, "worst_month": worst_m, "campaigns_90d": len(camps), "campaigns_over_limit": len(hot)})
    evidence = [ev(_series_ref("marked_spam"), "Marked Email as Spam count"), ev(_series_ref("received_email"), "Received Email count"),
                ev(ref("perf", "reports", "campaign90"), "per-campaign spam_complaint_rate, 90 days")]
    msg = f"worst month {worst_m} at {pct(rates[worst_m], 3)}; {len(hot)} of {len(camps)} recent campaigns at or above {pct(camp_max, 1)}"
    if rates[worst_m] >= fail_at:
        return outcome("fail", msg, value, evidence)
    if rates[worst_m] >= ok_max or hot:
        return outcome("warn", msg, value, evidence)
    return outcome("pass", msg, value, evidence)


@check("DLV-02")
def bounce_rate_low(snap: Snapshot, spec: dict):
    snap.require("totals", "perf")
    b_ok = th(spec, "pass", r"bounce < {n}%", 0.01)
    h_ok = th(spec, "pass", r"hard < {n}%", 0.01)
    camp_max = th(spec, "warn", r"a campaign > {n}%", 0.01)
    b_fail = th(spec, "fail", r"month > {n}%", 0.01)
    h_fail = th(spec, "fail", r"hard > {n}%", 0.01)
    recv, bounced = _months(snap, "received_email"), _months(snap, "bounced_email")
    hard = _months(snap, "bounced_email_by_type", dimension="Hard")
    try:
        dropped = _months(snap, "dropped_email")
    except MissingData:
        dropped = {}
    monthly = {}
    for m in recv:
        r, b = recv[m], bounced.get(m)
        if r is None or b is None or (r + b) == 0:
            continue
        h = hard.get(m)
        d = dropped.get(m)
        monthly[m] = {"bounce": b / (r + b), "hard": None if h is None else h / (r + b),
                      "dropped": None if d is None or (r + d) == 0 else d / (r + d)}
    if not monthly:
        raise MissingData("no full month with received and bounced email counts", _series_ref("received_email"))
    camps = [c for c, rate in _campaign_rates(snap, "bounce_rate") if rate > camp_max]
    worst_b = max(monthly, key=lambda m: monthly[m]["bounce"])
    hards = {m: v["hard"] for m, v in monthly.items() if v["hard"] is not None}
    worst_h = max(hards, key=hards.get) if hards else None
    value = as_dict({"monthly": monthly, "worst_bounce_month": worst_b, "worst_hard_month": worst_h,
                     "campaigns_over_limit": len(camps), "hard_bounce_known": bool(hards)})
    evidence = [ev(_series_ref("bounced_email"), "Bounced Email count"), ev(_series_ref("received_email"), "Received Email count"),
                ev(ref("totals", "aggregates", "bounced_email_by_type", "current"), "bounces by type"),
                ev(ref("perf", "reports", "campaign90"), "per-campaign bounce_rate, 90 days")]
    wb, wh = monthly[worst_b]["bounce"], (hards[worst_h] if worst_h else None)
    msg = (f"worst bounce month {worst_b} at {pct(wb)}" + (f", worst hard-bounce month {worst_h} at {pct(wh)}" if wh is not None
                                                         else ", hard bounces not reported")
           + f"; {len(camps)} recent campaign(s) above {pct(camp_max, 0)}")
    if wb > b_fail or (wh is not None and wh > h_fail):
        return outcome("fail", msg, value, evidence)
    if wb >= b_ok or (wh is not None and wh >= h_ok) or camps:
        return outcome("warn", msg, value, evidence)
    if wh is None:
        return outcome("warn", msg + " (can't confirm the hard-bounce limit)", value, evidence)
    return outcome("pass", msg, value, evidence)


@check("DLV-03")
def unsubscribe_rate_normal(snap: Snapshot, spec: dict):
    snap.require("totals", "perf")
    ok_max = th(spec, "pass", r"months < {n}%", 0.01)
    camp_limit = th(spec, "warn", r"campaigns > {n}%", 0.01)
    fail_at = th(spec, "fail", r"month > {n}%", 0.01)
    unsub, recv = _months(snap, "unsubscribed_email"), _months(snap, "received_email")
    rates = {m: unsub[m] / recv[m] for m in recv if recv[m] and unsub.get(m) is not None}
    if not rates:
        raise MissingData("no full month with both unsubscribes and received email", _series_ref("received_email"))
    camps = _campaign_rates(snap, "unsubscribe_rate")
    over = [c for c, r in camps if r > camp_limit]
    share = ratio(len(over), len(camps))
    worst_m = max(rates, key=rates.get)
    value = as_dict({"monthly": rates, "worst_month": worst_m, "campaigns_90d": len(camps),
                     "campaigns_over_limit": len(over), "share_over_limit": share})
    evidence = [ev(_series_ref("unsubscribed_email"), "Unsubscribed count"), ev(_series_ref("received_email"), "Received Email count"),
                ev(ref("perf", "reports", "campaign90"), "per-campaign unsubscribe_rate, 90 days")]
    msg = (f"worst month {worst_m} at {pct(rates[worst_m])}; {len(over)} of {len(camps)} recent campaigns above "
           f"{pct(camp_limit, 1)} (flows and list removals are in the account-wide rate)")
    if rates[worst_m] > fail_at:
        return outcome("fail", msg, value, evidence)
    if rates[worst_m] >= ok_max or (share is not None and share >= UNSUB_CAMPAIGN_SHARE_PASS):
        return outcome("warn", msg, value, evidence)
    return outcome("pass", msg, value, evidence)


def _excluded(provider: str) -> bool:
    p = provider.strip().lower()
    return p.startswith(INBOX_EXCLUDE_PREFIXES) or p in INBOX_EXCLUDE_NAMES


@check("DLV-04")
def inbox_providers_even(snap: Snapshot, spec: dict):
    """Open rate per provider = opened_email_by_inbox uniques / received_email_by_inbox count (the
    measurements step 38 collects), summed over the last 3 full months."""
    snap.require("totals")
    ok_rel = th(spec, "pass", r">= {n}% of average", 0.01)
    w_lo, _ = th_range(spec, "warn", r"at {n}-{n}%", 0.01)
    drop_max = th(spec, "fail", r"> {n}% monthly drop", 0.01)
    recv_rows = snap.rows("received_email_by_inbox")
    open_dims = {(r.get("dimensions") or [""])[0] for r in snap.rows("opened_email_by_inbox")}
    stats = {}
    for r in recv_rows:
        dim = (r.get("dimensions") or [""])[0]
        if not dim:
            continue
        rb = full_buckets(snap.monthly("received_email_by_inbox", "count", dimension=dim))[-INBOX_MONTHS:]
        ob = {b.label: b.value for b in full_buckets(snap.monthly("opened_email_by_inbox", "unique", dimension=dim))} \
            if dim in open_dims else {}
        stats[dim] = {"received": {b.label: b.value or 0 for b in rb}, "opened": {b.label: ob.get(b.label) or 0 for b in rb}}
    if not stats:
        raise MissingData("no received-email rows by inbox provider", ref("totals", "aggregates", "received_email_by_inbox"))
    total = sum(sum(s["received"].values()) for s in stats.values())
    if not total:
        raise MissingData("no received email in the last 3 full months", ref("totals", "aggregates", "received_email_by_inbox"))
    major = {d: s for d, s in stats.items() if sum(s["received"].values()) / total >= INBOX_MIN_SHARE and not _excluded(d)}
    if not major:
        raise MissingData("no non-Apple provider has >= 2% of volume", ref("totals", "aggregates", "received_email_by_inbox"))
    avg = ratio(sum(sum(s["opened"].values()) for s in major.values()), sum(sum(s["received"].values()) for s in major.values()))
    rows = {}
    for d, s in major.items():
        rate = ratio(sum(s["opened"].values()), sum(s["received"].values()))
        months = sorted(s["received"])
        last2 = [ratio(s["opened"][m], s["received"][m]) for m in months[-2:]]
        drop = None
        if len(last2) == 2 and last2[0] and last2[1] is not None:
            drop = 1 - last2[1] / last2[0]
        rows[d] = {"open_rate": rate, "vs_average": ratio(rate, avg), "mom_drop": drop,
                   "volume_share": sum(s["received"].values()) / total}
    verdicts = []
    for d, r in rows.items():
        rel = r["vs_average"]
        if rel is None:
            continue
        if rel < w_lo or (r["mom_drop"] is not None and r["mom_drop"] > drop_max):
            verdicts.append(("fail", d))
        elif rel < ok_rel:
            verdicts.append(("warn", d))
    value = as_dict({"non_apple_average_open_rate": avg, "providers": rows,
                     "excluded": sorted(d for d in stats if _excluded(d))})
    evidence = [ev(ref("totals", "aggregates", "received_email_by_inbox", "current"), "received by inbox provider"),
                ev(ref("totals", "aggregates", "opened_email_by_inbox", "current"), "opened (unique) by inbox provider")]
    weakest = min(rows, key=lambda d: rows[d]["vs_average"] if rows[d]["vs_average"] is not None else 9)
    msg = (f"{len(rows)} major non-Apple providers; weakest {weakest} opens at {pct(rows[weakest]['vs_average'], 0)} "
           f"of the average")
    if not verdicts:
        return outcome("pass", msg, value, evidence)
    v = worst(*[x[0] for x in verdicts])
    flagged = ", ".join(sorted(d for x, d in verdicts if x == v))
    return outcome(v, msg + f"; {v}: {flagged}", value, evidence)
