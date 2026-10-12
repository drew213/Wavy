"""Data integrity checks (DAT-*)."""
from __future__ import annotations

from datetime import timedelta

from .checks import NotApplicable, as_dict, check, ev, outcome, pct, ratio, worst
from .flows import CART_NAMES, CHECKOUT_NAMES, ORDER_NAMES, subscription_metric_ids
from .library import th, th_range
from .snapshot import FILES, MissingData, Snapshot, full_buckets, last_full, parse_dt, ref

# ---------------------------------------------------------------- DAT-01


@check("DAT-01")
def canonical_order_source(snap: Snapshot, spec: dict):
    """Snapshot track: other candidates' event counts aren't collected, so any other candidate is
    flagged for review (warn) rather than measured. The store platform is a client-confirmed fact
    passed as context['store_platform']; without it the check can't pass."""
    snap.require("totals")
    used = snap.metric_choice("placed_order")
    evidence = [ev(ref("totals", "metricChoices", "placed_order"), "step 38 Placed Order selection")]
    if not used:
        return outcome("fail", "step 38 selected no Placed Order metric, so revenue has no order source", {}, evidence)
    others = snap.at("totals", "metricChoices", "placed_order", "otherCandidates", default=[]) or []
    buckets = snap.monthly("placed_order", "count")
    full = full_buckets(buckets)
    if not full or full[-1].value is None:
        raise MissingData("no full month of Placed Order counts in step 38", ref("totals", "aggregates", "placed_order"))
    latest = full[-1]
    evidence.append(ev(ref("totals", "aggregates", "placed_order", "current", "rows", 0, "measurements", "count", latest.index),
                       f"{latest.label} Placed Order events"))
    conv = snap.at("perf", "conversionMetric", "id", default=None) if snap.has("perf") else None
    agree = None if conv is None else str(conv) == str(used.get("id"))
    if conv is not None:
        evidence.append(ev(ref("perf", "conversionMetric", "id"), "step 34 conversion metric"))
    platform = snap.context.get("store_platform")
    # Last-90-day events per candidate, when the collector measured them (same-named twins only).
    counts = {str(o.get("id")): o.get("events_last_90_days") for o in [used, *others] if "events_last_90_days" in o}
    known = {k: v for k, v in counts.items() if v is not None}
    share = (known[str(used.get("id"))] / sum(known.values())
             if str(used.get("id")) in known and sum(known.values()) else None)
    dormant = [o for o in others if counts.get(str(o.get("id"))) == 0]
    value = as_dict({
        "used": {"id": used.get("id"), "integration": used.get("integration")},
        "other_candidates": [{"id": o.get("id"), "integration": o.get("integration"),
                              "events_last_90_days": counts.get(str(o.get("id")))} for o in others],
        "latest_full_month": latest.label, "latest_full_month_events": latest.value,
        "last_3_full_months": {b.label: b.value for b in full[-3:]},
        "step34_metric_id": conv, "steps_agree": agree, "confirmed_platform": platform,
        "used_share_90d": share, "dormant_candidates": [o.get("id") for o in dormant],
    })
    if counts:
        evidence.append(ev(ref("totals", "metricChoices", "placed_order", "candidates"),
                           "last-90-day events per Placed Order candidate"))
    if latest.value == 0:
        return outcome("fail", f"the selected Placed Order metric had zero events in {latest.label}", value, evidence)
    if agree is False:
        return outcome("fail", "steps 34 and 38 used different Placed Order metrics", value, evidence)
    if agree is None:
        raise MissingData("step 34 conversion metric isn't in the snapshot, so step agreement can't be confirmed",
                          FILES["perf"])
    if platform and str(platform).strip().lower() != str(used.get("integration") or "").strip().lower():
        # A confirmed mismatch is worse than an unconfirmed platform: revenue is read from the wrong store.
        return outcome("fail", f"the selected metric is from {used.get('integration')}, but the client's store is {platform}",
                       value, evidence)
    if share is not None and share < 0.80:
        return outcome("fail", f"the selected Placed Order metric holds only {share:.0%} of the last 90 days' order "
                               "events; orders are split across sources", value, evidence)
    if dormant:
        names = ", ".join(f"{o.get('integration') or 'unknown'} ({o.get('id')})" for o in dormant)
        return outcome("warn", f"a dormant Placed Order metric is still in the account: {names}, with no events in 90 days. "
                               f"The audit reads {used.get('integration')}; any flow, segment or report still built on "
                               "the dormant copy shows zero", value, evidence)
    if share is not None and share < 0.95:
        return outcome("warn", f"the selected Placed Order metric holds {share:.0%} of the last 90 days' order events; "
                               "review the other source", value, evidence)
    if others and share is None:
        return outcome("warn", f"{len(others)} other Placed Order candidate(s) exist; their recent event share isn't "
                               "collected in the snapshot track, so review them (deep track measures it)", value, evidence)
    if not platform:
        return outcome("warn", f"one Placed Order source ({used.get('integration')}) with events in {latest.label} and both "
                               "steps agree, but the store platform isn't client-confirmed", value, evidence)
    return outcome("pass", f"{used.get('integration')} Placed Order is the only source, confirmed, active and used by both steps",
                   value, evidence)


# ---------------------------------------------------------------- DAT-02

_EVENT_KEYS = {**{n: "started_checkout" for n in CHECKOUT_NAMES}, **{n: "added_to_cart" for n in CART_NAMES},
               "viewed product": "viewed_product", "active on site": "active_on_site",
               **{n: "placed_order" for n in ORDER_NAMES}}


@check("DAT-02")
def triggers_on_live_metrics(snap: Snapshot, spec: dict):
    snap.require("api", "totals")
    flows = [f for f in snap.flows() if f.classified_live and f.trigger_type == "metric"]
    if not flows:
        raise NotApplicable("no live metric-triggered flows")
    subs = subscription_metric_ids(snap)
    items, unchecked, evidence = [], [], []
    for f in flows:
        name = (f.trigger_metric or "").strip().lower()
        item = {"flow_id": f.id, "metric_id": f.trigger_id, "metric": f.trigger_metric, "integration": f.trigger_integration}
        key = _EVENT_KEYS.get(name)
        if key is None and f.trigger_id in subs:
            key = f"subscription_{f.trigger_id}"
        if key is None:
            if (f.trigger_integration or "").lower() == "api":
                item["verdict"], item["why"] = "warn", "custom API metric: a person needs to confirm its source"
                items.append(item)
                evidence.append(ev(f.ref("triggers", 0), f"custom API trigger metric '{f.trigger_metric}'"))
            else:
                unchecked.append(item)  # no step 38 event count for this metric (e.g. shipping-app events)
            continue
        canon = snap.metric_choice(key)
        if not canon:
            item["verdict"], item["why"] = "warn", f"step 38 has no canonical {key} metric to compare with"
        elif str(canon.get("id")) != str(f.trigger_id):
            months = last_full(snap.monthly(key, "count"), 2)
            dormant = len(months) == 2 and all(b.value == 0 for b in months)
            item["verdict"], item["why"] = "warn", (
                f"triggers on '{f.trigger_metric}' ({f.trigger_integration}), not step 38's {canon.get('integration')} "
                + ("metric, which had zero events in the last 2 full months: step 38 likely selected a dormant source"
                   if dormant else "metric; the trigger metric's own events aren't counted in the snapshot track"))
            evidence.append(ev(ref("totals", "metricChoices", key), f"step 38 {key} selection"))
        else:
            months = last_full(snap.monthly(key, "count"), 2)
            if len(months) < 2 or any(b.value is None for b in months):
                item["verdict"], item["why"] = "warn", "fewer than two full months of event counts"
            elif all(b.value == 0 for b in months):
                item["verdict"], item["why"] = "fail", "canonical metric had zero events in the last 2 full months"
            elif months[-1].value == 0:
                item["verdict"], item["why"] = "warn", f"no events in {months[-1].label}"
            else:
                item["verdict"], item["why"] = "pass", f"canonical, {int(months[-1].value)} events in {months[-1].label}"
            evidence.append(ev(ref("totals", "aggregates", key, "current"), f"{key} monthly counts"))
        items.append(item)
        evidence.append(ev(f.ref("triggers", 0), f"trigger of flow {f.id}: {item['why']}"))
    if not items:
        raise NotApplicable("no live flow triggers on a metric step 38 counts or on a custom API metric")
    verdict = worst(*[i["verdict"] for i in items])
    bad = [i for i in items if i["verdict"] == verdict and verdict != "pass"]
    msg = (f"{len(items)} live metric-triggered flow(s) checked; all on canonical, active metrics" if verdict == "pass"
           else f"{len(bad)} of {len(items)} live flow trigger(s) {verdict}: " + "; ".join(sorted({i['why'] for i in bad}))[:400])
    return outcome(verdict, msg, as_dict({"flows": items, "unchecked": unchecked}), evidence[:40])


# ---------------------------------------------------------------- DAT-03


@check("DAT-03")
def order_line_items(snap: Snapshot, spec: dict):
    """Snapshot track: line items only. Currency consistency needs v2 events (deep), so a pass
    here says nothing about currency."""
    snap.require("orders")
    hist = snap.order_history()
    end = hist.end or snap.as_of
    if end is None:
        raise MissingData("placed-orders-24mo.json has no end date", ref("orders", "end"))
    recent = [has for t, has in hist.with_items if t >= end - timedelta(days=365)]
    if not recent:
        raise NotApplicable("no orders in the last 12 months")
    share = sum(recent) / len(recent)
    p = th(spec, "pass", r">= {n}% orders with items", 0.01)
    w_lo, _ = th_range(spec, "warn", r"{n}-{n}% orders with items", 0.01)
    value = as_dict({"orders_12mo": len(recent), "share_with_items": share, "currency_checked": False,
                     "duplicates_removed": hist.duplicates})
    evidence = [ev(ref("orders", "orders"), "orders[].i over the last 12 months")]
    if share >= p:
        return outcome("pass", f"{pct(share, 1)} of last-12-month orders carry line items (currency is deep-track only)", value, evidence)
    msg = f"only {pct(share, 1)} of last-12-month orders carry line items"
    if share < w_lo:
        return outcome("fail", msg + "; check the step 38 extractor before treating this as a store problem", value, evidence)
    return outcome("warn", msg, value, evidence)


# ---------------------------------------------------------------- DAT-04


@check("DAT-04")
def local_month_buckets(snap: Snapshot, spec: dict):
    """Partial periods: the engine marks every bucket's `full` flag (Snapshot.monthly), so the
    final partial month is labelled here and excluded from every full-month comparison."""
    snap.require("api", "totals")
    tz38 = snap.at("totals", "account", "timezone")
    tz24 = snap.at("api", "account", "items", 0, "attributes", "timezone")
    evidence = [ev(ref("totals", "account", "timezone"), "step 38 timezone"),
                ev(ref("api", "account", "items", 0, "attributes", "timezone"), "step 24 timezone")]
    if tz38 != tz24:
        return outcome("fail", f"step 24 ({tz24}) and step 38 ({tz38}) disagree on the account timezone",
                       {"step24": tz24, "step38": tz38}, evidence)
    tz = snap.tz
    misaligned, checked = [], 0
    for key, agg in (snap.at("totals", "aggregates", default={}) or {}).items():
        if not isinstance(agg, dict):
            continue
        for window in ("current", "prior"):
            for i, d in enumerate(((agg.get(window) or {}).get("dates")) or []):
                dt = parse_dt(d)
                if dt is None:
                    continue
                checked += 1
                local = dt.astimezone(tz)
                if (local.day, local.hour, local.minute, local.second) != (1, 0, 0, 0):
                    misaligned.append(ref("totals", "aggregates", key, window, "dates", i))
    if not checked:
        raise MissingData("step 38 has no aggregate dates", ref("totals", "aggregates"))
    partial = [b.label for b in snap.monthly("placed_order", "count") if not b.full]
    value = {"timezone": tz38, "buckets_checked": checked, "misaligned": len(misaligned), "partial_buckets": partial}
    if misaligned:
        evidence += [ev(r, "bucket not at local midnight on the 1st") for r in misaligned[:10]]
        return outcome("fail", f"{len(misaligned)} of {checked} monthly buckets aren't cut at local midnight in {tz38}",
                       value, evidence)
    evidence.append(ev(ref("totals", "aggregates", "placed_order", "current", "dates"), "bucket boundaries"))
    return outcome("pass", f"all {checked} buckets start at local midnight ({tz38}); partial buckets labelled: "
                           f"{', '.join(partial) or 'none'}", value, evidence)


# ---------------------------------------------------------------- DAT-05

# DAT-05 notes: step 38 flowSeriesWeekly365 fails in every saved snapshot and no check scores from it.
NON_SCORING_REPORTS = frozenset({"flowSeriesWeekly365"})
NON_SCORING_ERROR_LABELS = frozenset({"flow series weekly 365d"})
# Step 24 sections no snapshot check reads. profilesSample is a deliberate one-profile sample (always "truncated").
NON_SCORING_24 = frozenset({"templates"})
SCORING_24 = ("flows", "campaigns", "forms", "lists", "segments", "metrics", "templates", "account")
SNAPSHOT_STEP_FILES = {"24-": "api", "34-": "perf", "37-": "templates", "38-": "totals"}


@check("DAT-05")
def snapshot_complete(snap: Snapshot, spec: dict):
    present = [k for k in ("run", "api", "perf", "templates", "totals", "orders") if snap.has(k)]
    if not present:
        raise MissingData("the snapshot folder has none of the collector files")
    flags: list[dict] = []

    def flag(r: str, detail: str, scoring: bool = True):
        flags.append({"ref": r, "detail": detail, "scoring": scoring})

    for k in ("api", "perf", "templates", "totals", "orders"):
        if not snap.has(k):
            flag(FILES[k], f"{FILES[k]} is missing (its step didn't run or didn't save)")
    if snap.has("api"):
        api = snap.load("api")
        for sec in SCORING_24:
            node = api.get(sec)
            if not isinstance(node, dict):
                flag(ref("api", sec), f"step 24 {sec} section is missing", sec not in NON_SCORING_24)
                continue
            if node.get("truncated"):
                flag(ref("api", sec, "truncated"), f"step 24 {sec} truncated", sec not in NON_SCORING_24)
            if node.get("ok") is False:
                flag(ref("api", sec, "ok"), f"step 24 {sec} failed", sec not in NON_SCORING_24)
        if (api.get("flows") or {}).get("messageCapHit"):
            flag(ref("api", "flows", "messageCapHit"), "step 24 hit the flow message detail cap")
        for i, fd in enumerate((api.get("flows") or {}).get("details") or []):
            if fd.get("errors"):
                flag(ref("api", "flows", "details", i, "errors"), f"flow detail {i} has {len(fd['errors'])} error(s)")
            if (fd.get("actionFetch") or {}).get("ok") is False:
                flag(ref("api", "flows", "details", i, "actionFetch"), f"flow detail {i} action fetch failed")
    if snap.has("perf"):
        for name, rep in (snap.at("perf", "reports", default={}) or {}).items():
            if not isinstance(rep, dict) or rep.get("ok") is not True:
                flag(ref("perf", "reports", name), f"step 34 report {name} failed", name not in NON_SCORING_REPORTS)
            elif (((rep.get("value") or {}).get("links") or {}).get("next")):
                flag(ref("perf", "reports", name, "value", "links", "next"), f"step 34 report {name} has an unread next page")
    if snap.has("totals"):
        tot = snap.load("totals")
        for i, e in enumerate(tot.get("errors") or []):
            label = (e or {}).get("label") or "unlabelled"
            flag(ref("totals", "errors", i), f"step 38 error: {label}", label not in NON_SCORING_ERROR_LABELS)
        for name, rep in (tot.get("reports") or {}).items():
            if isinstance(rep, dict) and rep.get("error") and name not in NON_SCORING_REPORTS:
                flag(ref("totals", "reports", name, "error"), f"step 38 report {name} failed")
        orders = tot.get("orders") or {}
        if orders.get("truncated"):
            flag(ref("totals", "orders", "truncated"), "step 38 orders truncated")
        if orders.get("error"):
            flag(ref("totals", "orders", "error"), "step 38 orders errored")
        for ch in ("email", "sms"):
            if ((tot.get("campaigns") or {}).get(ch) or {}).get("truncated"):
                flag(ref("totals", "campaigns", ch, "truncated"), f"step 38 {ch} campaigns truncated")
        for sec in ("catalog", "coupons"):
            if (tot.get(sec) or {}).get("truncated"):
                flag(ref("totals", sec, "truncated"), f"step 38 {sec} truncated", scoring=False)
        for key, agg in (tot.get("aggregates") or {}).items():
            if isinstance(agg, dict) and (agg.get("error") or any(isinstance(agg.get(w), dict) and agg[w].get("error")
                                                                  for w in ("current", "prior"))):
                flag(ref("totals", "aggregates", key), f"step 38 aggregate {key} errored")
    if snap.has("templates"):
        errs = snap.at("templates", "errors", default=0)
        if (len(errs) if isinstance(errs, list) else errs or 0) > 0:
            flag(ref("templates", "errors"), "step 37 recorded template errors")
    if snap.has("orders"):
        od = snap.load("orders")
        if od.get("truncated"):
            flag(ref("orders", "truncated"), "placed-orders truncated")
        if od.get("error"):
            flag(ref("orders", "error"), "placed-orders errored")
    if snap.has("run"):
        for h, run in enumerate(snap.at("run", "history", default=[]) or []):
            for s, step in enumerate(run.get("steps") or []):
                if step.get("ok") is False:
                    f = step.get("file") or ""
                    flag(ref("run", "history", h, "steps", s), f"runner step {f} failed",
                         any(f.startswith(p) for p in SNAPSHOT_STEP_FILES))
    scoring = [f for f in flags if f["scoring"]]
    value = {"flags": flags[:50], "scoring_flags": len(scoring), "other_flags": len(flags) - len(scoring),
             "files_present": [FILES[k] for k in present]}
    evidence = [ev(f["ref"], f["detail"]) for f in (scoring or flags)[:25]] or [
        ev(FILES[present[0]], "every truncated/ok/error flag checked")]
    if scoring:
        return outcome("fail", f"{len(scoring)} scoring input(s) truncated, failed or missing: "
                               + "; ".join(f["detail"] for f in scoring[:5]), value, evidence)
    if flags:
        return outcome("warn", f"only non-scoring sections failed: " + "; ".join(f["detail"] for f in flags[:5]), value, evidence)
    return outcome("pass", "no truncation or errors in any collector output", value, evidence)


# ---------------------------------------------------------------- DAT-06


@check("DAT-06")
def attributed_revenue_reconciles(snap: Snapshot, spec: dict):
    snap.require("totals", "perf")
    total = {b.label: b for b in full_buckets(snap.monthly("placed_order", "sum_value"))}
    chan_rows = [r for r in snap.rows("placed_order_by_channel") if (r.get("dimensions") or [""])[0]]
    if not chan_rows:
        raise MissingData("step 38 has no attributed-channel rows", ref("totals", "aggregates", "placed_order_by_channel"))
    channels = {r["dimensions"][0]: {b.label: b.value for b in snap.monthly("placed_order_by_channel", "sum_value",
                                                                             dimension=r["dimensions"][0])}
                for r in chan_rows}
    win = snap.at("totals", "windows", "current")
    w_start, w_end = parse_dt(win.get("start"), snap.tz), parse_dt(win.get("end"), snap.tz)
    flows_by_month: dict[str, float] = {}
    flow_window_sum = 0.0
    for r in snap.rows("placed_order_by_flow"):
        dim = (r.get("dimensions") or [""])[0]
        if not dim:
            continue
        for b in snap.monthly("placed_order_by_flow", "sum_value", dimension=dim):
            if b.value is None:
                continue
            if b.full:
                flows_by_month[b.label] = flows_by_month.get(b.label, 0.0) + b.value
            if w_start is None or b.start >= w_start:  # whole window incl. the partial capture month
                flow_window_sum += b.value
    over_total, over_email, att_sum, tot_sum = [], [], 0.0, 0.0
    for label, b in total.items():
        if b.value is None:
            continue
        att = sum(v.get(label) or 0.0 for v in channels.values())
        att_sum += att
        tot_sum += b.value
        if att > b.value * (1 + 1e-9):
            over_total.append(label)
        email = (channels.get("$email_channel") or {}).get(label)
        if email is not None and flows_by_month.get(label, 0.0) > email * (1 + 1e-9):
            over_email.append(label)
    share = ratio(att_sum, tot_sum)
    if share is None:
        raise MissingData("no full months of total order value to compare with", ref("totals", "aggregates", "placed_order"))
    flow365 = sum(float((r.get("statistics") or {}).get("conversion_value") or 0) for r in snap.report_results("flow365"))
    days = (w_end - w_start).total_seconds() / 86400 if w_start and w_end else None
    # Step 34 is Klaviyo's rolling last 365 days; step 38's by-flow sum covers Nov 1 to capture.
    # Scale the step 38 sum linearly to 365 days before comparing (library note on DAT-06).
    scaled = flow_window_sum * 365 / days if days else None
    gap = abs(flow365 - scaled) / scaled if scaled else None
    s_lo, s_hi = th_range(spec, "pass", r"share {n}-{n}%", 0.01)
    g_pass = th(spec, "pass", r"gap <= {n}%", 0.01)
    s_warn_hi = th_range(spec, "warn", r"share {n}-{n}%", 0.01)[1]
    g_fail = th(spec, "fail", r"gap > {n}%", 0.01)
    value = as_dict({"attributed_share": share, "months": len(total), "months_attributed_over_total": over_total,
                     "months_flows_over_email": over_email, "flow365_step34": flow365,
                     "flow_window_step38_scaled_365": scaled, "report_gap": gap, "channels": sorted(channels)})
    evidence = [ev(ref("totals", "aggregates", "placed_order_by_channel", "current"), "attributed value by channel"),
                ev(ref("totals", "aggregates", "placed_order", "current"), "total Placed Order value"),
                ev(ref("totals", "aggregates", "placed_order_by_flow", "current"), "attributed value by flow"),
                ev(ref("perf", "reports", "flow365"), "step 34 flow conversion value, last 365 days")]
    problems = []
    if over_total:
        problems.append(("fail", f"attributed value exceeds total in {', '.join(over_total)}"))
    if share > s_warn_hi:
        problems.append(("fail", f"attributed share {pct(share, 1)} is above {pct(s_warn_hi, 0)}"))
    elif share > s_hi:
        problems.append(("warn", f"attributed share {pct(share, 1)} is {pct(s_hi, 0)}-{pct(s_warn_hi, 0)}"))
    elif share < s_lo:
        problems.append(("warn", f"attributed share {pct(share, 1)} is below the {pct(s_lo, 0)} floor of the pass band"))
    if over_email:
        problems.append(("warn", f"flow-attributed value exceeds email-attributed value in {', '.join(over_email)}"))
    if gap is None:
        problems.append(("warn", "step 34 vs step 38 flow revenue couldn't be compared"))
    elif gap > g_fail:
        problems.append(("fail", f"step 34 and step 38 flow revenue differ by {pct(gap, 0)}"))
    elif gap > g_pass:
        problems.append(("warn", f"step 34 and step 38 flow revenue differ by {pct(gap, 0)}"))
    if not problems:
        return outcome("pass", f"attributed {pct(share, 1)} of total, never above it; flow reports within {pct(gap, 0)}",
                       value, evidence)
    verdict = worst(*[p[0] for p in problems])
    return outcome(verdict, "; ".join(p[1] for p in problems), value, evidence)
