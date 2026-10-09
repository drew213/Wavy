"""List growth and capture checks (CAP-*)."""
from __future__ import annotations

from .checks import as_dict, check, ev, outcome, pct, ratio
from .library import th
from .snapshot import MissingData, Snapshot, full_buckets, ref


@check("CAP-01")
def signup_form_converting(snap: Snapshot, spec: dict):
    """Snapshot track: form type (popup/flyout vs embed) is only in step 31, so the most-viewed
    live form is treated as the primary capture form (library logic). Rates sum viewed_form_uniques
    and submits across a form's versions, which can count a viewer once per version."""
    snap.require("api", "totals")
    forms = snap.at("api", "forms", "items")
    live = {str(f.get("id")): i for i, f in enumerate(forms) if (f.get("attributes") or {}).get("status") == "live"}
    evidence = [ev(ref("api", "forms", "items"), "step 24 form statuses")]
    if not live:
        return outcome("fail", "no live sign-up form", {"live_forms": 0}, evidence)
    results = snap.at("totals", "reports", "formValues365", "data", "attributes", "results")
    per: dict[str, dict] = {}
    for r in results:
        fid = str((r.get("groupings") or {}).get("form_id"))
        if fid not in live:
            continue
        st = r.get("statistics") or {}
        d = per.setdefault(fid, {"views": 0.0, "submits": 0.0})
        d["views"] += float(st.get("viewed_form_uniques") or 0)
        d["submits"] += float(st.get("submits") or 0)
    evidence.append(ev(ref("totals", "reports", "formValues365", "data", "attributes", "results"), "365-day form views and submits"))
    p = th(spec, "pass", r">= {n}%", 0.01)
    f_lo = th(spec, "fail", r"< {n}%", 0.01)
    if not per or max(d["views"] for d in per.values()) == 0:
        return outcome("warn", f"{len(live)} live form(s), but none recorded a view in 365 days",
                       {"live_forms": len(live)}, evidence)
    primary_id, primary = max(per.items(), key=lambda kv: kv[1]["views"])
    rate = ratio(primary["submits"], primary["views"])
    value = as_dict({"live_forms": len(live), "primary_form_id": primary_id, "primary_views_365": primary["views"],
                     "primary_submits_365": primary["submits"], "primary_submit_rate": rate, "form_type_known": False,
                     "other_live_forms": {k: ratio(v["submits"], v["views"]) for k, v in per.items() if k != primary_id}})
    evidence.append(ev(ref("api", "forms", "items", live[primary_id]), "primary (most-viewed) live form"))
    note = " (form type needs step 31; the most-viewed live form is treated as the popup)"
    if rate >= p:
        return outcome("pass", f"primary live form converts {pct(rate)} of viewers" + note, value, evidence)
    if rate < f_lo:
        return outcome("fail", f"primary live form converts only {pct(rate)} of viewers" + note, value, evidence)
    return outcome("warn", f"primary live form converts {pct(rate)} of viewers" + note, value, evidence)


@check("CAP-02")
def list_growing(snap: Snapshot, spec: dict):
    snap.require("totals")
    n = int(th(spec, "pass", r"each of last {n} months"))
    keep = th(spec, "pass", r"gross >= {n}% of prior year", 0.01)
    fail_down = th(spec, "fail", r"gross down > {n}% YoY", 0.01)
    subs = {b.label: b for b in full_buckets(snap.monthly("subscribed_email", "unique"))}
    unsubs = {b.label: b for b in full_buckets(snap.monthly("unsubscribed_email", "unique"))}
    months = [lb for lb in subs if lb in unsubs][-n:]
    if len(months) < n or any(subs[m].value is None or unsubs[m].value is None for m in months):
        raise MissingData(f"fewer than {n} full months of subscribe and unsubscribe counts",
                          ref("totals", "aggregates", "subscribed_email", "current"))
    prior = {b.label[5:]: b for b in full_buckets(snap.monthly("subscribed_email", "unique", window="prior"))}
    if any(m[5:] not in prior or prior[m[5:]].value is None for m in months):
        raise MissingData("the prior window lacks the same months for a year-over-year comparison",
                          ref("totals", "aggregates", "subscribed_email", "prior"))
    net = {m: subs[m].value - unsubs[m].value for m in months}
    gross = sum(subs[m].value for m in months)
    gross_prior = sum(prior[m[5:]].value for m in months)
    yoy = ratio(gross, gross_prior)
    value = as_dict({"months": months, "net_by_month": net, "net_total": sum(net.values()), "gross": gross,
                     "gross_prior_year": gross_prior, "gross_vs_prior_year": yoy})
    evidence = [ev(ref("totals", "aggregates", "subscribed_email", "current", "rows", 0, "measurements", "unique"),
                   "Subscribed to Email Marketing uniques"),
                ev(ref("totals", "aggregates", "unsubscribed_email", "current", "rows", 0, "measurements", "unique"),
                   "Unsubscribed from Email uniques"),
                ev(ref("totals", "aggregates", "subscribed_email", "prior", "rows", 0, "measurements", "unique"),
                   "same months a year earlier")]
    yoy_txt = "no prior-year baseline" if yoy is None else f"gross subscribes at {pct(yoy, 0)} of last year"
    down = None if yoy is None else 1 - yoy
    if sum(net.values()) < 0 or (down is not None and down > fail_down):
        return outcome("fail", f"net growth over {months[0]}..{months[-1]} is {sum(net.values()):+.0f}; {yoy_txt}", value, evidence)
    if all(v > 0 for v in net.values()) and (yoy is None or yoy >= keep):
        return outcome("pass", f"net positive in each of the last {n} full months; {yoy_txt}", value, evidence)
    why = []
    if not all(v > 0 for v in net.values()):
        why.append("net positive on average but negative in " + ", ".join(m for m, v in net.items() if v <= 0))
    if down is not None and down > 1 - keep:
        why.append(yoy_txt)
    return outcome("warn", "; ".join(why) or yoy_txt, value, evidence)
