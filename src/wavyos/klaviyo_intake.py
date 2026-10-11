"""What onboarding learns from one read-only Klaviyo key.

Klaviyo already holds the store's orders (the Shopify integration's Placed Order events), its
flows, and what those flows and campaigns earned, so a single private key is enough to audit an
account. Every call here reads; the client refuses anything else before it reaches the network.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from .collector.boundaries import boundary_to_utc, month_start
from .collector.metrics import describe, ecommerce_candidates, flatten, pick_metric, recent_activity
from .connectors.klaviyo import KlaviyoError
from .scope import Scope
from .storage import BACKFILL_DAYS, KLAVIYO, count_orders, ingest_klaviyo_orders

PLACED_ORDER = "Placed Order"
MIN_HEALTH_PARTS = 3  # a score from one part (flow names alone) reads as 100 and means nothing
REVENUE_TIMEFRAME = {"key": "last_365_days"}

# The flows the Flow Architect builds, and the names agencies and Klaviyo templates give them.
GOLDEN_FLOWS: dict[str, tuple[str, re.Pattern]] = {
    "browse_abandonment": ("Browse Abandonment", re.compile(r"brows", re.I)),
    "added_to_cart": ("Added to Cart", re.compile(r"(added.?to.?cart|abandon\w*.?cart|cart.?abandon)", re.I)),
    "abandoned_checkout": ("Abandoned Checkout", re.compile(r"checkout", re.I)),
    "replenishment": ("Replenishment", re.compile(r"(replenish|reorder|refill|restock|post.?purchase)", re.I)),
    "customer_winback": ("Winback", re.compile(r"(win.?back|lapsed|we miss you|come back)", re.I)),
}

Report = Callable[..., None]


def _noop(*_a, **_k) -> None:
    return None


def choose_order_metric(client, now: datetime, platform: Optional[str] = None) -> dict:
    """Which Placed Order metric is this store's live one. {metric_id, status, reason, candidates}.

    Stores that changed platform keep a dormant copy (WooCommerce stores often carry an old Shopify one),
    so when several copies exist each one's last-90-day order count decides, then the store platform.
    """
    metrics = [flatten(r) for r in client.metrics()]
    activity = None
    if len(ecommerce_candidates(metrics, PLACED_ORDER)) > 1:
        activity = recent_activity(client, [m["id"] for m in ecommerce_candidates(metrics, PLACED_ORDER)], now)
    choice = pick_metric(metrics, PLACED_ORDER, "ecommerce", activity=activity, platform=platform)
    described = describe(choice)
    for cand in described["candidates"]:
        cand["orders_last_90_days"] = (activity or {}).get(cand["id"])
    used = choice.get("metric")
    return {"metric_id": str(used["id"]) if used else None, "status": choice["status"], "reason": choice["reason"],
            "candidates": described["candidates"]}


def run(session: Session, scope: Scope, client, *, progress: Report = _noop, now: Optional[datetime] = None,
        expect_account: Optional[str] = None, platform: Optional[str] = None) -> dict[str, Any]:
    """Read the account end to end. Returns the `klaviyo` section of the dossier.

    progress(stage, status, detail, **metrics) is called as each stage starts and ends.
    expect_account stops the read when the key opens a different account than the one connected.
    platform is the store platform the brand named, used to pick between order sources.
    """
    now = now or datetime.now(timezone.utc)
    out: dict[str, Any] = {"source": "klaviyo", "read_at": now.isoformat(), "unavailable": {}}

    progress("connect", "active", "Opening the Klaviyo account")
    account = client.account()
    if expect_account and account.get("id") != expect_account:
        raise ValueError(f"the Klaviyo key for {expect_account} now opens a different account")
    out["account"] = account
    progress("connect", "done", f"Connected to {account.get('name') or account.get('id')}",
             account_name=account.get("name"), currency=account.get("currency"), timezone=account.get("timezone"))

    progress("orders", "active", "Finding the store's order source in Klaviyo")
    tz = account.get("timezone") or "UTC"
    choice = choose_order_metric(client, now, platform)
    metric_id = choice["metric_id"]
    out["placed_order_metric_id"] = metric_id
    out["metric_choice"] = choice
    if metric_id is None:
        out["unavailable"]["orders"] = (
            "several Placed Order sources are sending orders and none matches the store platform; Wavy needs to "
            "pin the right one" if choice["status"] == "ambiguous" else
            "this account has no Placed Order metric (no store integration sending orders)")
        progress("orders", "skipped", "Couldn't tell which order source is the store's" if choice["status"] == "ambiguous"
                 else "No Placed Order metric: the store isn't sending orders to Klaviyo")
    else:
        out["totals_12m"] = _totals(client, metric_id, now, tz, out, progress)
        expected = out["totals_12m"].get("orders") if out.get("totals_12m") else None

        def on_page(cursor, _added) -> None:
            progress("orders", "active", "", orders_imported=cursor.orders_stored, orders_expected=expected,
                     pages=cursor.pages_pulled)

        before = count_orders(session, scope, KLAVIYO)
        cursor, finished = ingest_klaviyo_orders(session, scope, client, metric_id, now=now, on_page=on_page)
        stored = count_orders(session, scope, KLAVIYO)
        out["orders"] = {"imported": stored, "new_this_run": stored - before, "pages": cursor.pages_pulled,
                         "backfill_complete": finished, "window_days": BACKFILL_DAYS}
        word = "Imported" if finished else "Imported so far:"
        progress("orders", "done", f"{word} {stored:,} orders from the last 12 months",
                 orders_imported=stored, backfill_complete=finished)

    progress("flows", "active", "Reading every flow in the account")
    flows = client.flows()
    out["flows"] = [_flow_summary(f) for f in flows][:200]
    out["coverage"] = coverage(out["flows"])
    live = sum(1 for f in out["flows"] if f["status"] == "live")
    have = sum(1 for c in out["coverage"].values() if c["live"])
    progress("flows", "done", f"{len(flows)} flows, {live} live. {have} of the 5 core flows are running",
             flows_total=len(flows), flows_live=live, core_live=have,
             coverage={k: v["live"] for k, v in out["coverage"].items()})

    progress("performance", "active", "Pulling 12 months of flow revenue")
    if metric_id is not None:
        out["revenue_12m"] = _revenue(client, metric_id, out, progress)
        email = out["revenue_12m"].get("email_total")
        total = (out.get("totals_12m") or {}).get("revenue")
        if email is not None and total:
            out["revenue_12m"]["email_share"] = round(email / total, 4)
        progress("performance", "done", _revenue_line(out), **{
            "flow_revenue": out["revenue_12m"].get("flows"), "campaign_revenue": out["revenue_12m"].get("campaigns"),
            "email_share": out["revenue_12m"].get("email_share")})
    else:
        progress("performance", "skipped", "No order metric to attribute revenue to")
    return out


def coverage(flows: list[dict]) -> dict[str, dict]:
    """For each core flow: the account's flows whose names match it, and whether any of them is live."""
    result = {}
    for key, (label, pattern) in GOLDEN_FLOWS.items():
        matches = [f for f in flows if pattern.search(f.get("name") or "")]
        if key == "abandoned_checkout":
            matches = [f for f in matches if not GOLDEN_FLOWS["added_to_cart"][1].search(f.get("name") or "")]
        result[key] = {"label": label, "live": any(f["status"] == "live" for f in matches),
                       "matches": [{"id": f["id"], "name": f["name"], "status": f["status"]} for f in matches][:5]}
    return result


def health(klaviyo: Optional[dict], repeat_purchase_rate: Optional[float]) -> dict[str, Any]:
    """A 0-100 score from what was measured. Parts that couldn't be measured are left out, not guessed."""
    parts = []
    if klaviyo and klaviyo.get("coverage") is not None:
        live = sum(1 for c in klaviyo["coverage"].values() if c["live"])
        parts.append({"key": "flow_coverage", "label": "Core flow coverage", "max": 40,
                      "score": round(40 * live / len(GOLDEN_FLOWS), 1), "value": f"{live} of {len(GOLDEN_FLOWS)} live",
                      "ratio": live / len(GOLDEN_FLOWS)})
    share = ((klaviyo or {}).get("revenue_12m") or {}).get("email_share")
    if share is not None:
        parts.append({"key": "email_share", "label": "Email share of revenue", "max": 30,
                      "score": round(30 * min(share / 0.30, 1.0), 1), "value": f"{share:.1%} (benchmark 30%)",
                      "ratio": min(share / 0.30, 1.0)})
    if repeat_purchase_rate is not None:
        parts.append({"key": "repeat_rate", "label": "Repeat purchase rate", "max": 30,
                      "score": round(30 * min(repeat_purchase_rate / 0.30, 1.0), 1),
                      "value": f"{repeat_purchase_rate:.1%} (benchmark 30%)",
                      "ratio": min(repeat_purchase_rate / 0.30, 1.0)})
    possible = sum(p["max"] for p in parts)
    enough = len(parts) >= MIN_HEALTH_PARTS
    score = round(100 * sum(p["score"] for p in parts) / possible, 1) if possible and enough else None
    return {"score": score, "parts": parts, "measured": bool(klaviyo),
            "withheld": None if enough else f"only {len(parts)} of {MIN_HEALTH_PARTS} parts could be measured"}


# ---------------------------------------------------------------- reads

def totals_window(now: datetime, tz: str) -> tuple[str, str]:
    """The first of the month 11 months back, at local midnight, to now: under Klaviyo's one-year limit."""
    return boundary_to_utc(month_start(now, tz, -11), tz), now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _totals(client, metric_id: str, now: datetime, tz: str, out: dict, progress: Report) -> dict:
    start, end = totals_window(now, tz)
    try:
        agg = client.metric_aggregate(metric_id, measurements=["count", "sum_value"], start=start, end=end,
                                      interval="month", timezone_name=tz)
    except (KlaviyoError, ValueError) as e:
        out["unavailable"]["totals_12m"] = str(e)
        return {}
    rows = agg.get("data") or []
    measures = (rows[0].get("measurements") if rows else None) or {}
    counts = [int(x or 0) for x in measures.get("count") or []]
    sums = [float(x or 0) for x in measures.get("sum_value") or []]
    months = [{"month": str(d)[:7], "orders": counts[i] if i < len(counts) else 0,
               "revenue": round(sums[i], 2) if i < len(sums) else 0.0} for i, d in enumerate(agg.get("dates") or [])]
    totals = {"orders": sum(counts), "revenue": round(sum(sums), 2), "months": months,
              "window": {"start": start, "end": end, "timezone": tz}}
    progress("orders", "active", f"{totals['orders']:,} orders and ${totals['revenue']:,.0f} in the last 12 months",
             orders_expected=totals["orders"], revenue_12m=totals["revenue"], months=months)
    return totals


def _revenue(client, metric_id: str, out: dict, progress: Report) -> dict:
    result: dict[str, Any] = {}
    try:
        report = client.flow_values_report(statistics=["conversion_value", "conversions", "recipients"],
                                           timeframe=REVENUE_TIMEFRAME, conversion_metric_id=metric_id,
                                           group_by=["flow_id", "flow_name"])
        by_flow: dict[str, float] = {}
        names: dict[str, str] = {}
        for row in report.get("results") or []:
            groupings = row.get("groupings") or {}
            fid = str(groupings.get("flow_id") or "")
            if groupings.get("flow_name"):
                names[fid] = str(groupings["flow_name"])
            by_flow[fid] = by_flow.get(fid, 0.0) + float((row.get("statistics") or {}).get("conversion_value") or 0)
        result["flows"] = round(sum(by_flow.values()), 2)
        result["by_flow"] = {k: round(v, 2) for k, v in sorted(by_flow.items(), key=lambda kv: -kv[1])[:25]}
        result["flow_names"] = {k: names[k] for k in result["by_flow"] if k in names}
        progress("performance", "active", f"Flows earned ${result['flows']:,.0f}. Pulling campaign revenue",
                 flow_revenue=result["flows"])
    except KlaviyoError as e:
        out["unavailable"]["flow_revenue"] = str(e)
    try:
        report = client.campaign_values_report(statistics=["conversion_value", "conversions", "recipients"],
                                               timeframe=REVENUE_TIMEFRAME, conversion_metric_id=metric_id)
        result["campaigns"] = round(sum(float((r.get("statistics") or {}).get("conversion_value") or 0)
                                        for r in report.get("results") or []), 2)
    except KlaviyoError as e:
        out["unavailable"]["campaign_revenue"] = str(e)
    if "flows" in result or "campaigns" in result:
        result["email_total"] = round(result.get("flows", 0.0) + result.get("campaigns", 0.0), 2)
    return result


def _flow_summary(f: dict) -> dict:
    a = f.get("attributes") or {}
    return {"id": str(f.get("id")), "name": str(a.get("name") or ""), "status": str(a.get("status") or "").lower(),
            "trigger_type": a.get("trigger_type"), "archived": bool(a.get("archived"))}


def _revenue_line(out: dict) -> str:
    r = out.get("revenue_12m") or {}
    if "email_total" not in r:
        return "Revenue reports weren't available for this key"
    line = f"Email earned ${r['email_total']:,.0f} in 12 months"
    if r.get("email_share") is not None:
        line += f", {r['email_share']:.1%} of store revenue"
    return line
