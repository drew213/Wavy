"""Capture and reach: each form's views, submits and rate, list growth net of unsubscribes, SMS consent,
and the engaged segments that are the real sendable reach."""
from __future__ import annotations

from collections import defaultdict

from ..audit.snapshot import MissingData, Snapshot, ref
from .base import Analysis, num, pct, ratio, stats_of
from .series import try_monthly

MODULE = "capture_reach"
MIN_VIEWS = 1000
LOW_RATE = 0.02


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    snap.require("totals")
    names = {str(f.get("id")): ((f.get("attributes") or {}).get("name") or str(f.get("id")),
                                (f.get("attributes") or {}).get("status"))
             for f in (snap.at("api", "forms", "items", default=[]) or [])} if snap.has("api") else {}
    report = snap.at("totals", "reports", "formValues365", default=None)
    forms = []
    if isinstance(report, dict) and not report.get("error"):
        rolled: dict[str, dict] = defaultdict(lambda: {"views": 0.0, "submits": 0.0})
        for r in ((report.get("data") or {}).get("attributes") or {}).get("results") or []:
            fid = str((r.get("groupings") or {}).get("form_id") or "")
            s = stats_of(r)
            rolled[fid]["views"] += num(s.get("viewed_form_uniques") or s.get("viewed_form"))
            rolled[fid]["submits"] += num(s.get("submits"))
        for fid, v in rolled.items():
            name, status = names.get(fid, (fid, None))
            forms.append({"id": fid, "name": name, "status": status, "views": v["views"], "submits": v["submits"],
                          "submit_rate": ratio(v["submits"], v["views"])})
    forms.sort(key=lambda f: -f["views"])
    a.table("forms", forms)
    a.fact("form_submits_365d", sum(f["submits"] for f in forms) if forms else None, "submits", "Form submits, 365 days",
           source=ref("totals", "reports", "formValues365"))
    a.fact("live_forms", sum(1 for f in names.values() if f[1] == "live") if names else None, "forms", "Live forms")

    subs = try_monthly(snap, "subscribed_email", "unique")
    unsubs = try_monthly(snap, "unsubscribed_email", "unique")
    if subs:
        growth = [{"month": mo, "subscribed": v, "unsubscribed": (unsubs or {}).get(mo), "net": v - (unsubs or {}).get(mo, 0.0)
                   if unsubs else None} for mo, v in sorted(subs.items())]
        a.table("list_growth", growth)
        a.fact("net_subscribers_12m", sum(g["net"] for g in growth) if unsubs else None, "profiles",
               "Email subscribers gained net of unsubscribes, full months", source=ref("totals", "aggregates", "subscribed_email"))
    sms = try_monthly(snap, "subscribed_sms", "unique")
    a.fact("sms_subscribed_12m", sum(sms.values()) if sms else None, "profiles", "SMS consents, full months")
    segs = [g for g in (snap.at("totals", "groupCounts", "segments", default=[]) or []) if g.get("profileCount") is not None]
    a.table("engaged_segments", segs)
    if segs:
        a.fact("largest_engagement_segment", max(g["profileCount"] for g in segs), "profiles",
               "Largest engagement segment (sendable reach proxy)", source=ref("totals", "groupCounts", "segments"))
    if not forms and not subs and not segs:
        raise MissingData("no form report, subscription aggregates or segment counts", ref("totals"))

    weak = [f for f in forms if f["views"] >= MIN_VIEWS and (f["submit_rate"] or 0) < LOW_RATE]
    if weak:
        f = weak[0]
        a.fact("weak_form_rate", f["submit_rate"], "ratio", f"Submit rate of '{f['name']}'")
        a.find("weak_forms", "Busy sign-up forms that rarely convert",
               f"'{f['name']}' was seen by {int(f['views']):,} people in 12 months but converted {pct(f['submit_rate'])}"
               + (f"; {len(weak) - 1} more form(s) are under {pct(LOW_RATE, 0)}." if len(weak) > 1 else "."),
               "; ".join(f"{x['name']}: {int(x['views']):,} views, {int(x['submits']):,} submits" for x in weak[:4]) + ".",
               "Test the offer and the timing (exit intent or 15-30 seconds in) on the busiest form first; a two-step "
               "form (email, then SMS) usually lifts both.", severity="medium", category="capture", effort_days=2,
               lever="welcome", facts=[f"{MODULE}.weak_form_rate"],
               readout={"metric": "submit rate of the tested form", "days": 30})
