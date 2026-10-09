"""SMS checks (SMS-*). All are n/a when the precondition (Klaviyo SMS in use) is absent."""
from __future__ import annotations

import re

from .checks import NotApplicable, as_dict, check, ev, outcome, pct, ratio
from .library import th
from .snapshot import MissingData, Snapshot, full_buckets, ref

_STOP = re.compile(r"\b(stop|opt[\s-]?out)\b", re.I)
# SMS-04 logic: SMS revenue in each of the last 3 months.
SMS_REVENUE_MONTHS = 3


@check("SMS-02")
def sms_flow_compliance(snap: Snapshot, spec: dict):
    snap.require("api")
    sends = [(f, s) for f in snap.flows() if f.live for s in f.live_sends if s.channel == "sms"]
    if not sends:
        raise NotApplicable("no live SMS messages in live Klaviyo flows")
    no_optout, no_quiet, no_prefix = [], [], []
    for f, s in sends:
        msg = s.message
        r = f.ref("actions", s.index, "data", "message")
        if not msg.get("add_opt_out_language") and not _STOP.search(str(msg.get("body") or "")):
            no_optout.append(r)
        if not msg.get("sms_quiet_hours_enabled"):
            no_quiet.append(r)
        if not msg.get("add_org_prefix"):
            no_prefix.append(r)
    value = {"live_sms": len(sends), "missing_opt_out": len(no_optout), "missing_quiet_hours": len(no_quiet),
             "missing_org_prefix": len(no_prefix)}
    evidence = ([ev(r, "no opt-out language") for r in no_optout] + [ev(r, "quiet hours off") for r in no_quiet]
                + [ev(r, "no brand prefix") for r in no_prefix])[:20] or [ev(sends[0][0].ref(), "live SMS settings")]
    if no_optout or no_quiet:
        return outcome("fail", f"{len(no_optout)} live SMS without opt-out language, {len(no_quiet)} without quiet hours",
                       value, evidence)
    if no_prefix:
        return outcome("warn", f"{len(no_prefix)} of {len(sends)} live SMS lack the brand prefix", value, evidence)
    return outcome("pass", f"all {len(sends)} live flow SMS carry opt-out language, brand prefix and quiet hours", value, evidence)


def _klaviyo_sms_received(snap: Snapshot) -> dict[str, float]:
    snap.require("totals")
    if not snap.metric_choice("received_sms"):
        raise NotApplicable("not on Klaviyo SMS (step 38 found no Received SMS metric)")
    recv = {b.label: b.value for b in full_buckets(snap.monthly("received_sms", "count"))}
    if not any(v for v in recv.values()):
        raise NotApplicable("Klaviyo received no SMS in the window (SMS may run on another platform)")
    return recv


@check("SMS-03")
def sms_optout_failure_low(snap: Snapshot, spec: dict):
    recv = _klaviyo_sms_received(snap)
    o_ok = th(spec, "pass", r"opt-out < {n}%", 0.01)
    f_ok = th(spec, "pass", r"failure < {n}%", 0.01)
    o_fail = th(spec, "fail", r"opt-out > {n}%", 0.01)
    f_fail = th(spec, "fail", r"failure > {n}%", 0.01)
    unsub = {b.label: b.value for b in full_buckets(snap.monthly("unsubscribed_sms", "count"))}
    try:
        failed = {b.label: b.value for b in full_buckets(snap.monthly("failed_sms", "count"))}
    except MissingData:
        failed = {}
    monthly = {}
    for m, r in recv.items():
        if not r:
            continue
        f = failed.get(m)
        monthly[m] = {"opt_out": ratio(unsub.get(m), r), "failure": None if f is None else f / (r + f)}
    worst_o = max((v["opt_out"] for v in monthly.values() if v["opt_out"] is not None), default=None)
    worst_f = max((v["failure"] for v in monthly.values() if v["failure"] is not None), default=None)
    if worst_o is None:
        raise MissingData("no SMS unsubscribe counts for months with SMS received", ref("totals", "aggregates", "unsubscribed_sms"))
    value = as_dict({"monthly": monthly, "worst_opt_out": worst_o, "worst_failure": worst_f})
    evidence = [ev(ref("totals", "aggregates", k, "current"), f"{k} monthly count") for k in ("received_sms", "unsubscribed_sms", "failed_sms")]
    msg = f"worst monthly opt-out {pct(worst_o)}, worst failure {pct(worst_f)}"
    if worst_o > o_fail or (worst_f is not None and worst_f > f_fail):
        return outcome("fail", msg, value, evidence)
    if worst_o >= o_ok or (worst_f is not None and worst_f >= f_ok) or worst_f is None:
        return outcome("warn", msg + (" (failure counts not reported)" if worst_f is None else ""), value, evidence)
    return outcome("pass", msg, value, evidence)


@check("SMS-04")
def sms_revenue_measured(snap: Snapshot, spec: dict):
    recv = _klaviyo_sms_received(snap)
    sms = {b.label: b.value for b in full_buckets(snap.monthly("placed_order_by_channel", "sum_value", dimension="$sms_channel"))}
    email = {b.label: b.value for b in full_buckets(snap.monthly("placed_order_by_channel", "sum_value", dimension="$email_channel"))}
    months = [m for m in recv][-SMS_REVENUE_MONTHS:]
    sending = [m for m in months if recv.get(m)]
    missing = [m for m in sending if not sms.get(m)]
    total_sms = sum(v or 0 for v in sms.values())
    total_email = sum(v or 0 for v in email.values())
    value = as_dict({"months": months, "months_with_sends": sending, "months_without_sms_revenue": missing,
                     "sms_share_of_attributed": ratio(total_sms, total_sms + total_email)})
    evidence = [ev(ref("totals", "aggregates", "placed_order_by_channel", "current"), "attributed value by channel"),
                ev(ref("totals", "aggregates", "received_sms", "current"), "Received SMS count")]
    if total_sms == 0:
        return outcome("fail", "Klaviyo SMS was sent but no SMS-attributed revenue appears in the window "
                               "(step 34 reports email only; investigate before reporting SMS as zero)", value, evidence)
    if missing:
        return outcome("warn", f"no SMS revenue in {', '.join(missing)} despite sends", value, evidence)
    return outcome("pass", f"SMS earns {pct(value['sms_share_of_attributed'], 1)} of attributed revenue, every recent month", value, evidence)
