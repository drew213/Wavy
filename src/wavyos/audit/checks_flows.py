"""Core automation checks (FLW-*). Flow types come from conventions.flow_classifier (flows.py)."""
from __future__ import annotations

from datetime import timedelta

from .checks import as_dict, check, ev, outcome, pct, ratio
from .flows import (CART_NAMES, CHECKOUT_NAMES, CORE_TYPES, INTENT_TYPES, ORDER_NAMES, ORDER_TRIGGER_NAMES,
                    REPLENISHMENT_MIN_DAYS, FlowInfo,
                    _conditions, _days, ancestors, excludes_metric, is_zero_since, iter_filter_conditions, live_of,
                    metric_ids_named, metric_names, segment_kind)
from .library import th, th_range
from .snapshot import MissingData, Snapshot, last_full, parse_dt, ref

# FLW-04 logic: re-entry is limited when people can't re-enter for >= 7 days.
BROWSE_REENTRY_MIN_DAYS = 7
# FLW-06 logic: the lapsed pool is buyers whose last order is 1.5x to 4x the median repeat gap ago.
LAPSED_POOL_MAX_X = 4.0
# FLW-07 logic: the actions that suppress or remove a profile at the end of a sunset flow.
SUPPRESSION_ACTIONS = ("update-profile", "list-update")
# CMP-01/03/04 and FLW-07 look at campaigns sent in the last 90 days.
RECENT_CAMPAIGN_DAYS = 90


def summary(f: FlowInfo) -> dict:
    return as_dict({"id": f.id, "name": f.name, "kind": f.kind, "trigger": f.trigger_metric or f.trigger_type,
                    "emails_main_path": f.main_path_emails, "recipients90": f.recipients90,
                    "first_send_days": f.first_send_days, "needs_review": f.needs_review})


def _flow_ev(f: FlowInfo, detail: str) -> dict:
    return ev(f.ref(), f"flow {f.id} ({f.kind}): {detail}")


def _none_live(kind: str, label: str, snap: Snapshot) -> object:
    others = [f for f in snap.flows() if f.kind == kind and not f.classified_live]
    detail = f"; {len(others)} non-live {label} flow(s) exist" if others else ""
    if kind == "post_purchase":
        order = [f for f in snap.flows() if f.classified_live and f.kind in ("replenishment", "winback")
                 and f.trigger_type == "metric"]
        if order:
            detail += (f"; {len(order)} live order-triggered flow(s) are classified replenishment/winback because "
                       f"their first email waits {REPLENISHMENT_MIN_DAYS}+ days")
    return outcome("fail", f"no live {label} flow{detail}", {"non_live": [summary(f) for f in others[:10]]},
                   [ev(ref("api", "flows", "items"), f"no live {label} flow among step 24 flows")])


def _min_emails(spec: dict, band: str = "pass") -> int:
    return int(th(spec, band, r">= {n} emails?"))


@check("FLW-01")
def welcome_live(snap: Snapshot, spec: dict):
    snap.require("api")
    flows = live_of(snap, "welcome")
    if not flows:
        return _none_live("welcome", "welcome", snap)
    snap.require("perf")
    need = _min_emails(spec)
    good = [f for f in flows if f.main_path_emails >= need and f.sending]
    best = max(good or flows, key=lambda f: (f.main_path_emails >= need, f.sending, f.main_path_emails, f.recipients90 or 0))
    value = {"flows": [summary(f) for f in flows], "best": best.id}
    evidence = [_flow_ev(best, f"{best.main_path_emails} live emails on its longest path, "
                               f"{best.recipients90 or 0} recipients in 90 days"),
                ev(ref("perf", "reports", "flow90"), "90-day flow recipients")]
    if good:
        return outcome("pass", f"live welcome flow with {best.main_path_emails} emails, sending in the last 90 days", value, evidence)
    why = []
    if best.main_path_emails < need:
        why.append(f"only {best.main_path_emails} live email(s) on its longest path")
    if not best.sending:
        why.append("zero recipients in the last 90 days")
    return outcome("warn", "live welcome flow, but " + " and ".join(why), value, evidence)


@check("FLW-02")
def abandoned_checkout_live(snap: Snapshot, spec: dict):
    """Coverage = people reached (each flow's largest per-message 90-day recipients, summed over
    live checkout flows on the canonical metric) / Started Checkout uniques summed over the last 3
    full months. Monthly uniques overlap at the edges, so coverage is slightly understated."""
    snap.require("api")
    flows = live_of(snap, "abandoned_checkout")
    if not flows:
        return _none_live("abandoned_checkout", "abandoned checkout", snap)
    snap.require("perf", "totals")
    need = _min_emails(spec)
    cov_pass = th(spec, "pass", r"coverage >= {n}%", 0.01)
    cov_fail = th(spec, "fail", r"coverage < {n}%", 0.01)
    canon = snap.metric_choice("started_checkout")
    on_canon = [f for f in flows if canon and str(f.trigger_id) == str(canon.get("id"))]
    starters_b = last_full(snap.monthly("started_checkout", "unique"), 3)
    if len(starters_b) < 3 or any(b.value is None for b in starters_b):
        raise MissingData("fewer than 3 full months of Started Checkout uniques", ref("totals", "aggregates", "started_checkout"))
    starters = sum(b.value for b in starters_b)
    if not starters:
        why = (f"step 38's checkout metric ({(canon or {}).get('integration')}) has no uniques in the last 3 full months "
               "and the live flow triggers on a different metric, so coverage can't be measured (see DAT-02)"
               if not on_canon else "no Started Checkout uniques in the last 3 full months")
        raise MissingData(why, ref("totals", "aggregates", "started_checkout"))
    use = on_canon or flows
    reached = sum(f.recipients90 or 0 for f in use)
    coverage = ratio(reached, starters)
    emails = max(f.main_path_emails for f in use)
    value = as_dict({"flows": [summary(f) for f in flows], "canonical_metric_id": (canon or {}).get("id"),
                     "on_canonical": [f.id for f in on_canon], "reached_90d": reached, "starters_3mo": starters,
                     "coverage": coverage, "emails_main_path": emails})
    evidence = [_flow_ev(f, f"{f.main_path_emails} live emails, {f.recipients90 or 0} reached in 90 days") for f in use[:5]]
    evidence.append(ev(ref("totals", "aggregates", "started_checkout", "current", "rows", 0, "measurements", "unique"),
                       f"Started Checkout uniques {starters_b[0].label}..{starters_b[-1].label}"))
    cov =f"reaches {pct(coverage, 1)} of checkout starters"
    if coverage < cov_fail:
        return outcome("fail", f"live checkout flow {cov}", value, evidence)
    why = []
    if not on_canon:
        why.append("no live checkout flow triggers on the canonical checkout metric (DAT-02)")
    if emails < need:
        why.append(f"only {emails} live email(s)")
    if coverage < cov_pass:
        why.append(cov)
    if why:
        return outcome("warn", "; ".join(why), value, evidence)
    return outcome("pass", f"live with {emails} emails; {cov}", value, evidence)


@check("FLW-03")
def abandoned_cart_live(snap: Snapshot, spec: dict):
    snap.require("api")
    flows = live_of(snap, "abandoned_cart")
    if not flows:
        return _none_live("abandoned_cart", "abandoned cart", snap)
    need = _min_emails(spec)
    checkout_ids = metric_ids_named(snap, CHECKOUT_NAMES)
    rows = []
    for f in flows:
        where = excludes_metric(f, checkout_ids)
        if not where and any(a.get("type") == "trigger-split" for a in f.definition.get("actions") or []):
            where = [("trigger-split", None)]  # library: "or a trigger split"
        rows.append((f, where))
    best_f, best_where = max(rows, key=lambda r: (bool(r[1]), r[0].main_path_emails >= need, r[0].main_path_emails))
    value = {"flows": [dict(summary(f), checkout_exclusion=[w for w, _ in wh]) for f, wh in rows], "best": best_f.id}
    evidence = [_flow_ev(best_f, f"{best_f.main_path_emails} live emails; checkout exclusion: "
                                 f"{', '.join(sorted({w for w, _ in best_where})) or 'none'}")]
    if best_where and best_f.main_path_emails >= need:
        return outcome("pass", f"live cart flow with {best_f.main_path_emails} emails that excludes checkout starters", value, evidence)
    why = []
    if not best_where:
        why.append("doesn't exclude people who started checkout (doubles up with FLW-02)")
    if best_f.main_path_emails < need:
        why.append(f"only {best_f.main_path_emails} live email(s)")
    return outcome("warn", "live cart flow, but it " + " and ".join(why), value, evidence)


def _reentry_days(f: FlowInfo):
    rc = f.definition.get("reentry_criteria") or {}
    unit = str(rc.get("unit") or "").lower()
    if unit == "alltime":
        return float("inf")
    if rc.get("duration") is not None and unit:
        return float(rc["duration"]) * {"day": 1, "days": 1, "hour": 1 / 24, "hours": 1 / 24, "week": 7, "weeks": 7}.get(unit, 1)
    best = None
    for c in _conditions(f.definition.get("profile_filter") or {}):
        if c.get("type") == "profile-not-in-flow":
            d = _days(c.get("timeframe_filter") or {})
            if d is not None:
                best = max(best or 0, d)
    return best


@check("FLW-04")
def browse_live(snap: Snapshot, spec: dict):
    snap.require("api")
    flows = live_of(snap, "browse")
    if not flows:
        return _none_live("browse", "browse abandonment", snap)
    funnel_ids = metric_ids_named(snap, CART_NAMES | CHECKOUT_NAMES)
    rows = []
    for f in flows:
        re_days = _reentry_days(f)
        rows.append((f, bool(excludes_metric(f, funnel_ids)), re_days is not None and re_days >= BROWSE_REENTRY_MIN_DAYS, re_days))
    best = max(rows, key=lambda r: (r[1] and r[2], r[1], r[2], r[0].main_path_emails))
    f, excl, limited, re_days = best
    value = as_dict({"flows": [dict(summary(x), excludes_cart_or_checkout=e, reentry_days=d if d != float("inf") else "once")
                               for x, e, _, d in rows], "best": f.id})
    evidence = [_flow_ev(f, f"exclusions: {'yes' if excl else 'no'}; re-entry limit: {'yes' if limited else 'no'}"),
                ev(f.ref("reentry_criteria"), "re-entry setting")]
    if excl and limited:
        return outcome("pass", "live browse flow excludes cart/checkout starters and limits re-entry", value, evidence)
    why = [w for w, bad in (("doesn't exclude cart/checkout starters", not excl),
                            (f"re-entry isn't limited to >= {BROWSE_REENTRY_MIN_DAYS} days", not limited)) if bad]
    return outcome("warn", "live browse flow, but it " + " and ".join(why), value, evidence)


def _first_time_path(snap: Snapshot, f: FlowInfo, order_ids: set[str]) -> bool:
    """A conditional split (or flow filter) on lifetime/recent order count, not a since-flow-start exit."""
    for where, c, _ in iter_filter_conditions(f):
        if where not in ("split", "flow") or str(c.get("metric_id")) not in order_ids:
            continue
        if (c.get("timeframe_filter") or {}).get("operator") == "flow-start":
            continue
        if where == "split":
            return True
        mf = c.get("measurement_filter") or {}
        if (mf.get("operator"), mf.get("value")) in (("equals", 1), ("less-than", 2), ("equals", 0)):
            return True
    return False


@check("FLW-05")
def post_purchase_live(snap: Snapshot, spec: dict):
    snap.require("api")
    flows = live_of(snap, "post_purchase")
    if not flows:
        return _none_live("post_purchase", "post-purchase", snap)
    need = _min_emails(spec)
    order_ids = metric_ids_named(snap, ORDER_TRIGGER_NAMES)
    rows = [(f, _first_time_path(snap, f, order_ids)) for f in flows]
    best, split = max(rows, key=lambda r: (r[1] and r[0].main_path_emails >= need, r[1], r[0].main_path_emails))
    value = {"flows": [dict(summary(f), first_time_path=s) for f, s in rows], "best": best.id}
    evidence = [_flow_ev(best, f"{best.main_path_emails} live emails; first-time buyer path: {'yes' if split else 'no'}")]
    if split and best.main_path_emails >= need:
        return outcome("pass", f"live post-purchase flow with {best.main_path_emails} emails and a first-time buyer path", value, evidence)
    why = [w for w, bad in (("one path for every buyer", not split),
                            (f"only {best.main_path_emails} live email(s)", best.main_path_emails < need)) if bad]
    return outcome("warn", "live post-purchase flow, but " + " and ".join(why), value, evidence)


def _segment_lapse_days(snap: Snapshot, f: FlowInfo) -> float | None:
    segs = {str(s.get("id")): s for s in snap.at("api", "segments", "items", default=[]) or []}
    seg = segs.get(str(f.trigger_id)) or {}
    names = metric_names(snap)
    best = None
    for c in _conditions((seg.get("attributes") or {}).get("definition") or {}):
        if names.get(str(c.get("metric_id")), "").lower() in ORDER_TRIGGER_NAMES and is_zero_since(c):
            d = _days(c.get("timeframe_filter") or {})
            if d is not None:
                best = max(best or 0, d)
    return best


@check("FLW-06")
def winback_live(snap: Snapshot, spec: dict):
    """Timing: days from the last order to the first winback email (Placed Order trigger: the
    flow's first-send delay; segment trigger: the segment's no-order window plus that delay),
    compared with the median 1st-to-2nd order gap from placed-orders-24mo.json."""
    snap.require("api")
    flows = live_of(snap, "winback")
    if not flows:
        return _none_live("winback", "winback", snap)
    snap.require("perf", "orders")
    lo_x, hi_x = th_range(spec, "pass", r"{n}-{n}x median", 1.0)
    median, _, n = snap.median_repeat_gap()
    if median is None:
        raise MissingData("no profile with two observed orders, so there's no median repeat gap", ref("orders", "orders"))
    hist = snap.order_history()
    end = hist.end or snap.as_of
    pool = sum(1 for t in hist.by_profile.values()
               if lo_x * median <= (end - t[-1]).total_seconds() / 86400 <= LAPSED_POOL_MAX_X * median)
    rows = []
    for f in flows:
        lapse = f.first_send_days
        if f.trigger_type == "segment":
            seg_days = _segment_lapse_days(snap, f)
            lapse = None if seg_days is None else seg_days + (f.first_send_days or 0)
        timed = lapse is not None and lo_x * median <= lapse <= hi_x * median
        rows.append((f, lapse, timed))
    best, lapse, timed = max(rows, key=lambda r: (r[2] and r[0].sending and r[0].main_path_emails >= 2, r[2], r[0].sending,
                                                  r[0].main_path_emails))
    value = as_dict({"flows": [dict(summary(f), days_after_last_order=l, timed=t) for f, l, t in rows],
                     "median_repeat_gap_days": median, "repeat_buyers": n, "window_days": [lo_x * median, hi_x * median],
                     "lapsed_pool": pool, "best": best.id})
    evidence = [_flow_ev(best, f"first email about {lapse if lapse is None else round(lapse)} days after the last order"),
                ev(ref("orders", "orders"), f"median 1st-to-2nd order gap {median:.0f} days over {n} repeat buyers"),
                ev(ref("perf", "reports", "flow90"), "90-day flow recipients")]
    win = f"{lo_x * median:.0f}-{hi_x * median:.0f} days"
    if timed and best.sending and best.main_path_emails >= 2:
        return outcome("pass", f"live winback sends its first email inside the {win} window", value, evidence)
    why = []
    if not best.sending:
        why.append("zero recipients in the last 90 days")
    if not timed:
        why.append(f"first email lands {'at an unknown time' if lapse is None else f'about {lapse:.0f} days'} after the "
                   f"last order, outside {win}")
    if best.main_path_emails < 2:
        why.append("only one live email")
    return outcome("warn", "live winback, but " + "; ".join(why), value, evidence)


@check("FLW-07")
def sunset_live(snap: Snapshot, spec: dict):
    snap.require("api")
    flows = live_of(snap, "sunset")
    rows = []
    for f in flows:
        supp = [j for j, a in enumerate(f.definition.get("actions") or [])
                if a.get("type") in SUPPRESSION_ACTIONS and (a.get("data") or {}).get("status", "live") == "live"]
        rows.append((f, supp))
    if any(s for _, s in rows):
        f, supp = next(r for r in rows if r[1])
        return outcome("pass", "live sunset flow with a suppression/update-profile step",
                       {"flows": [dict(summary(x), suppression_steps=len(s)) for x, s in rows]},
                       [_flow_ev(f, "segment-triggered sunset"), ev(f.ref("actions", supp[0]), "suppression step")])
    if rows:
        f = rows[0][0]
        return outcome("warn", "live sunset flow, but it has no suppression or profile-update step",
                       {"flows": [summary(x) for x, _ in rows]}, [_flow_ev(f, "no suppression step")])
    snap.require("totals")
    names = metric_names(snap)
    unengaged = {str(s.get("id")): i for i, s in enumerate(snap.at("api", "segments", "items", default=[]) or [])
                 if segment_kind((s.get("attributes") or {}).get("definition") or {}, names) == "sunset"}
    end = snap.as_of
    excluded_by = 0
    for c in snap.at("totals", "campaigns", "email", "items", default=[]) or []:
        a = c.get("attributes") or {}
        t = parse_dt(a.get("send_time"))
        if a.get("status") != "Sent" or t is None or end is None or t < end - timedelta(days=RECENT_CAMPAIGN_DAYS):
            continue
        if set(map(str, (a.get("audiences") or {}).get("excluded") or [])) & set(unengaged):
            excluded_by += 1
    value = {"unengaged_segments": list(unengaged), "recent_campaigns_excluding_them": excluded_by}
    evidence = [ev(ref("api", "segments", "items"), "segment definitions"),
                ev(ref("totals", "campaigns", "email", "items"), "campaign exclusions, last 90 days")]
    if unengaged and excluded_by:
        return outcome("warn", f"no sunset flow, but {excluded_by} recent campaign(s) exclude an unengaged segment", value, evidence)
    return outcome("fail", "no sunset flow and no unengaged segment excluded from campaigns", value, evidence)


@check("FLW-08")
def flow_revenue_share(snap: Snapshot, spec: dict):
    snap.require("perf")
    p = th(spec, "pass", r">= {n}%", 0.01)
    w_lo, _ = th_range(spec, "warn", r"{n}-{n}%", 0.01)
    drop = th(spec, "warn", r"dropped > {n} pts", 0.01)

    def total(rep: str) -> float:
        return sum(float((r.get("statistics") or {}).get("conversion_value") or 0) for r in snap.report_results(rep))

    shares = {}
    for days in (365, 90):
        f, c = total(f"flow{days}"), total(f"campaign{days}")
        shares[days] = ratio(f, f + c)
    if shares[365] is None:
        raise MissingData("no email-attributed revenue in the 365-day reports", ref("perf", "reports"))
    s365, s90 = shares[365], shares[90]
    value = as_dict({"flow_share_365": s365, "flow_share_90": s90})
    evidence = [ev(ref("perf", "reports", f"{k}{d}"), f"{k} conversion value, {d} days")
                for k in ("flow", "campaign") for d in (365, 90)]
    msg = f"flows earn {pct(s365, 1)} of email revenue over 365 days ({pct(s90, 1)} over 90)"
    if s365 < w_lo:
        return outcome("fail", msg, value, evidence)
    dropped = s90 is not None and (s365 - s90) > drop
    if s365 < p or dropped:
        return outcome("warn", msg + ("; the 90-day share dropped more than " f"{drop * 100:.0f} points" if dropped else ""),
                       value, evidence)
    return outcome("pass", msg, value, evidence)


@check("FLW-09")
def flows_exit_on_purchase(snap: Snapshot, spec: dict):
    snap.require("api", "totals")
    canon = snap.metric_choice("placed_order")
    if not canon:
        raise MissingData("step 38 selected no canonical Placed Order metric", ref("totals", "metricChoices", "placed_order"))
    canon_id = str(canon.get("id"))
    order_ids = metric_ids_named(snap, ORDER_NAMES) | {canon_id}
    flows = [f for kind in INTENT_TYPES for f in live_of(snap, kind)]
    if not flows:
        raise MissingData("no live welcome, browse, cart or checkout flow to check", ref("api", "flows"))
    rows = []
    for f in flows:
        conds = iter_filter_conditions(f)
        exits = [(w, c, j) for w, c, j in conds if str(c.get("metric_id")) in order_ids
                 and (c.get("timeframe_filter") or {}).get("operator") == "flow-start"
                 and (is_zero_since(c) or w == "split")]
        flow_level = [c for w, c, _ in exits if w == "flow"]
        if flow_level:
            canonical = any(str(c.get("metric_id")) == canon_id for c in flow_level)
            status = "exit" if canonical else "noncanonical"
        else:
            anc = ancestors(f)
            ids = [str(a.get("id")) for a in f.definition.get("actions") or []]
            # A split guards every send after it; a message filter guards only its own send.
            guard_at = {ids[j] for w, c, j in exits if j is not None and w == "split"}
            msg_guard = {ids[j] for w, c, j in exits if j is not None and w == "message"}
            guarded = [s for s in f.live_sends if s.action_id in msg_guard or anc.get(s.action_id, set()) & guard_at]
            if f.live_sends and len(guarded) == len(f.live_sends):
                canonical = all(str(c.get("metric_id")) == canon_id for _, c, _ in exits)
                status = "guarded" if canonical else "noncanonical"
            elif guarded:
                status = "partial"
            else:
                status = "none"
        rows.append((f, status))
    abandonment = ("browse", "abandoned_cart", "abandoned_checkout")
    fails = [f for f, s in rows if s == "none" and f.kind in abandonment]
    warns = [f for f, s in rows if s in ("partial", "noncanonical") or (s == "none" and f.kind not in abandonment)]
    value = {"flows": [dict(summary(f), purchase_exit=s) for f, s in rows], "canonical_order_metric": canon_id}
    evidence = [_flow_ev(f, f"purchase exit: {s}") for f, s in rows][:20]
    if fails:
        return outcome("fail", f"{len(fails)} live abandonment flow(s) keep messaging people who already bought", value, evidence)
    if warns:
        return outcome("warn", f"{len(warns)} live intent flow(s) exit on a non-canonical order metric, guard only some "
                               "sends, or (welcome) have no purchase exit", value, evidence)
    return outcome("pass", f"all {len(rows)} live intent flows exit on the canonical Placed Order metric", value, evidence)


@check("FLW-10")
def flow_guardrails(snap: Snapshot, spec: dict):
    """Marketing sends = live emails in live core flows. The first email of a cart/checkout flow
    is exempt from smart sending; post-purchase emails are excluded from the transactional test
    because a post-purchase message can legitimately be transactional."""
    snap.require("api", "perf")
    p = th(spec, "pass", r">= {n}% smart sending", 0.01)
    f_lo = th(spec, "fail", r"< {n}% smart sending", 0.01)
    flows = [f for f in snap.flows() if f.classified_live and f.kind in CORE_TYPES]
    if not flows:
        raise MissingData("no live core flows", ref("api", "flows"))
    smart = total = 0
    transactional, drafts, silent = [], [], []
    for f in flows:
        first = f.first_email()
        for s in f.sends:
            if not s.live:
                drafts.append(ev(f.ref("actions", s.index), f"{s.status or 'non-live'} send in live flow {f.id}"))
                continue
            if s.channel != "email":
                continue
            if s.message.get("transactional") and f.kind != "post_purchase":
                transactional.append(ev(f.ref("actions", s.index), f"marketing email flagged transactional in flow {f.id}"))
            exempt = f.kind in ("abandoned_checkout", "abandoned_cart") and first is not None and s.action_id == first.action_id
            if not exempt:
                total += 1
                smart += 1 if s.message.get("smart_sending_enabled") else 0
            mid = str(s.message.get("id") or "")
            if mid and f.recipients90_by_message.get(mid, 0) == 0:
                silent.append(ev(f.ref("actions", s.index), f"live email with zero 90-day recipients in flow {f.id}"))
    share = ratio(smart, total)
    value = as_dict({"smart_sending_share": share, "marketing_emails": total, "transactional_flagged": len(transactional),
                     "non_live_sends": len(drafts), "live_zero_recipients": len(silent), "flows": len(flows)})
    evidence = (transactional + drafts + silent)[:20] or [ev(ref("api", "flows", "details"), "live core flow send settings")]
    if share is None:
        raise MissingData("no marketing emails in live core flows", ref("api", "flows", "details"))
    extras = [f"{n} {label}" for label, n in (("marketing emails flagged transactional", len(transactional)),
                                               ("non-live sends in live flows", len(drafts)),
                                               ("live emails with zero 90-day recipients", len(silent))) if n]
    detail = "; ".join([f"smart sending on {pct(share, 0)} of {total} marketing flow emails"] + extras)
    if transactional or share < f_lo:
        return outcome("fail", detail, value, evidence)
    if share < p or drafts or silent:
        return outcome("warn", detail, value, evidence)
    return outcome("pass", detail, value, evidence)
