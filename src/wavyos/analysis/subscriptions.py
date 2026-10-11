"""Subscriptions, when the subscription app sends events to Klaviyo: starts, renewals, cancellations,
failed payments and holds by month, and whether failed or held subscribers get any message."""
from __future__ import annotations

import re
from collections import defaultdict

from ..audit.snapshot import MissingData, Snapshot, ref
from .base import Analysis
from .series import try_monthly

MODULE = "subscriptions"
TYPES = (("failed", re.compile(r"fail|declin|payment.*(error|issue)|dunning|charge.?error", re.I)),
         ("on_hold", re.compile(r"hold|paus|skip", re.I)),
         ("cancelled", re.compile(r"cancel|expir", re.I)),
         ("started", re.compile(r"start|creat|activat|new subscri", re.I)),
         ("renewed", re.compile(r"renew|charge|recurr|billing|success", re.I)))


def kind_of(name: str) -> str:
    return next((k for k, rx in TYPES if rx.search(name or "")), "other")


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    snap.require("totals")
    metrics = {str(m.get("id")): m.get("name") or "" for m in snap.at("totals", "metrics", default=[]) or []}
    keys = [k for k in (snap.at("totals", "aggregates", default={}) or {}) if k.startswith("subscription_")]
    if not keys:
        raise MissingData("no subscription events reach Klaviyo" + (" although the brand runs subscriptions"
                                                                    if context.get("sells_subscriptions") else ""),
                          ref("totals", "aggregates"))
    monthly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_kind_ids: dict[str, list[str]] = defaultdict(list)
    for key in keys:
        mid = key.removeprefix("subscription_")
        kind = kind_of(metrics.get(mid, ""))
        by_kind_ids[kind].append(mid)
        for mo, v in (try_monthly(snap, key, "count") or {}).items():
            monthly[mo][kind] += v
    months = sorted(monthly)
    a.table("monthly", [{"month": mo, **dict(monthly[mo])} for mo in months])
    a.table("metrics", [{"id": mid, "name": metrics.get(mid), "kind": k} for k, ids in by_kind_ids.items() for mid in ids])
    recent = months[-3:]
    totals = {k: sum(monthly[mo].get(k, 0.0) for mo in recent) for k in ("started", "cancelled", "renewed", "failed", "on_hold")}
    for k, v in totals.items():
        a.fact(f"{k}_3m", v if by_kind_ids.get(k) else None, "events", f"Subscription {k.replace('_', ' ')} events, last 3 full months",
               window=f"{recent[0]}..{recent[-1]}" if recent else "", source=ref("totals", "aggregates"))

    if by_kind_ids.get("started") and by_kind_ids.get("cancelled") and totals["cancelled"] > totals["started"] > 0:
        a.find("subscribers_shrinking", "More subscriptions end than start",
               f"Over the last 3 full months, {int(totals['cancelled']):,} subscriptions were cancelled and "
               f"{int(totals['started']):,} started.",
               "Subscription start and cancel events from the subscription app, by month.",
               "Add a cancellation save flow (pause or skip offer, swap product) and a pre-renewal reminder that "
               "highlights what's coming.", severity="high", category="retention", effort_days=3,
               facts=[f"{MODULE}.cancelled_3m", f"{MODULE}.started_3m"],
               readout={"metric": "net subscriptions a month", "days": 60})
    try:
        triggers = {str(f.trigger_id) for f in snap.flows() if f.classified_live and f.trigger_id}
    except MissingData:
        triggers = set()
    for kind, label in (("failed", "failed renewals"), ("on_hold", "subscriptions on hold")):
        ids = by_kind_ids.get(kind) or []
        if ids and totals[kind] > 0 and snap.has("api") and not (set(ids) & triggers):
            a.find(f"{kind}_unmessaged", f"Customers with {label} get no message",
                   f"{int(totals[kind]):,} {label} events in the last 3 full months, and no live flow is triggered by them.",
                   f"Metrics: {', '.join(metrics.get(i, i) for i in ids)}; none is a live flow trigger.",
                   ("Build a failed-payment flow: update-your-card email at once, a reminder at 3 days, a last notice before "
                    "the subscription lapses." if kind == "failed" else
                    "Build an on-hold flow that tells the customer why it paused and gives a one-click way to resume."),
                   severity="high", category="retention", effort_days=2, facts=[f"{MODULE}.{kind}_3m"],
                   readout={"metric": f"{label} recovered", "days": 45})
