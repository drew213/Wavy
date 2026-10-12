"""Per-flow and per-message results over 365 and 90 days: revenue per recipient, engagement, unsubscribe,
spam and bounce rates, which flows are slipping, which are weak, which messages drive people away.
Revenue is Klaviyo-attributed (send-time basis) and is not incremental."""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Optional

from ..audit.snapshot import MissingData, Snapshot, ref
from .base import Analysis, num, pct, ratio, stats_of, usd

MODULE = "flow_history"
MIN_RECIPIENTS = 500
DECLINE = 0.6        # 90-day RPR below 60% of the 365-day RPR
WEAK = 0.25          # RPR below a quarter of the median flow
UNSUB_HIGH = 0.01
SPAM_HIGH = 0.001
SUMS = ("recipients", "delivered", "bounced", "opens_unique", "clicks_unique", "conversion_value", "unsubscribes",
        "spam_complaints", "conversion_uniques")


def roll(results: list[dict], key: str, name_key: str) -> dict[str, dict]:
    """Sum a values report's rows by a grouping key; rates are recomputed from the sums."""
    out: dict[str, dict] = {}
    for r in results:
        g, s = r.get("groupings") or {}, stats_of(r)
        k = g.get(key)
        if not k:
            continue
        row = out.setdefault(k, {"id": k, "name": g.get(name_key) or k, "channels": set(), **{x: 0.0 for x in SUMS}})
        row["channels"].add(g.get("send_channel") or "email")
        for x in SUMS:
            row[x] += num(s.get(x))
    for row in out.values():
        row["channels"] = sorted(row["channels"])
        d = row["delivered"] or row["recipients"]
        row.update(rpr=ratio(row["conversion_value"], row["recipients"]), open_rate=ratio(row["opens_unique"], d),
                   click_rate=ratio(row["clicks_unique"], d), unsubscribe_rate=ratio(row["unsubscribes"], d),
                   spam_rate=ratio(row["spam_complaints"], d), bounce_rate=ratio(row["bounced"], row["recipients"]))
    return out


def names_from_api(snap: Snapshot) -> dict[str, str]:
    try:
        return {str(f.get("id")): (f.get("attributes") or {}).get("name") or str(f.get("id"))
                for f in snap.at("api", "flows", "items", default=[]) or []}
    except MissingData:
        return {}


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    year = snap.report_results("flow365")
    try:
        quarter: Optional[list[dict]] = snap.report_results("flow90")
    except MissingData:
        quarter = None
    names = names_from_api(snap)
    flows365 = roll(year, "flow_id", "flow_name")
    flows90 = roll(quarter, "flow_id", "flow_name") if quarter is not None else {}
    for fid, row in flows365.items():
        if row["name"] == fid and fid in names:
            row["name"] = names[fid]
        q = flows90.get(fid)
        row["rpr_90"] = q["rpr"] if q else None
        row["recipients_90"] = q["recipients"] if q else 0
    flows = sorted(flows365.values(), key=lambda r: -r["conversion_value"])
    a.table("flows", flows)
    msgs = sorted(roll(year, "flow_message_id", "flow_message_name").values(), key=lambda r: -r["recipients"])
    flow_of = {(r.get("groupings") or {}).get("flow_message_id"): (r.get("groupings") or {}).get("flow_id") for r in year}
    for m in msgs:
        m["flow_id"] = flow_of.get(m["id"])
        m["flow_name"] = (flows365.get(m["flow_id"]) or {}).get("name")
    a.table("messages", msgs)

    total = sum(r["conversion_value"] for r in flows)
    a.fact("flows_with_revenue_365d", sum(1 for r in flows if r["conversion_value"] > 0), "flows",
           "Flows with attributed revenue in 365 days", time_basis="send", source=ref("perf", "reports", "flow365"))
    a.fact("flow_revenue_365d", total, "USD", "Flow-attributed revenue, 365 days", time_basis="send",
           source=ref("perf", "reports", "flow365"))
    if flows and total:
        a.fact("top_flow_share_365d", flows[0]["conversion_value"] / total, "ratio",
               f"Share of flow revenue from the top flow ({flows[0]['name']})", time_basis="send")
    sized = [r for r in flows if r["recipients"] >= MIN_RECIPIENTS and r["rpr"] is not None]
    median = statistics.median([r["rpr"] for r in sized]) if len(sized) >= 3 else None
    a.fact("median_flow_rpr_365d", median, "USD", "Median revenue per recipient across flows with 500+ recipients",
           time_basis="send")

    falling = [r for r in sized if r["rpr_90"] is not None and r["recipients_90"] >= MIN_RECIPIENTS
               and r["rpr"] and r["rpr_90"] < DECLINE * r["rpr"]]
    if falling:
        top = sorted(falling, key=lambda r: -(r["rpr"] - r["rpr_90"]) * r["recipients_90"])[:3]
        for i, r in enumerate(top):
            a.fact(f"declining.{i}.rpr_365d", r["rpr"], "USD", f"{r['name']}: revenue per recipient, 365 days", time_basis="send")
            a.fact(f"declining.{i}.rpr_90d", r["rpr_90"], "USD", f"{r['name']}: revenue per recipient, 90 days", time_basis="send")
        a.find("declining_flows", "Flows earning less per send than last year",
               f"{len(falling)} flow(s) earn under {DECLINE:.0%} of their 12-month revenue per recipient over the last 90 days, "
               f"led by {top[0]['name']}.",
               "; ".join(f"{r['name']}: {usd(r['rpr_90'])} vs {usd(r['rpr'])} per recipient" for r in top) + ".",
               "Check each flow's trigger and filters still match (a changed metric or offer often explains it), then "
               "refresh the first message's subject, offer and hero product.",
               severity="medium", category="flow_optimization",
               facts=[f"{MODULE}.declining.{i}.rpr_90d" for i in range(len(top))],
               readout={"metric": "90-day revenue per recipient of the refreshed flows", "days": 60})
    if median:
        weak = [r for r in sized if r["rpr"] < WEAK * median and r["recipients"] >= 2 * MIN_RECIPIENTS]
        if weak:
            weak = sorted(weak, key=lambda r: -r["recipients"])[:3]
            a.find("weak_flows", "High-volume flows that barely earn",
                   f"{len(weak)} flow(s) send to many people but earn under a quarter of the median flow per recipient.",
                   "; ".join(f"{r['name']}: {int(r['recipients']):,} recipients at {usd(r['rpr'])} each" for r in weak)
                   + f"; median flow {usd(median)}.",
                   "Decide for each: rebuild it around a clear next step (product, offer, reason to click) or turn it off "
                   "to stop spending sends and attention on it.",
                   severity="medium", category="flow_optimization", facts=[f"{MODULE}.median_flow_rpr_365d"],
                   readout={"metric": "revenue per recipient of the rebuilt flows", "days": 60})
    risky = [m for m in msgs if m["recipients"] >= MIN_RECIPIENTS and
             ((m["unsubscribe_rate"] or 0) > UNSUB_HIGH or (m["spam_rate"] or 0) > SPAM_HIGH)]
    if risky:
        risky = sorted(risky, key=lambda m: -((m["unsubscribe_rate"] or 0) + 10 * (m["spam_rate"] or 0)))[:5]
        a.find("messages_driving_people_away", "Flow messages that drive unsubscribes or complaints",
               f"{len(risky)} flow message(s) run above {pct(UNSUB_HIGH, 0)} unsubscribes or {pct(SPAM_HIGH, 1)} spam complaints.",
               "; ".join(f"{m['flow_name'] or ''} / {m['name']}: unsubscribe {pct(m['unsubscribe_rate'], 2)}, "
                         f"spam {pct(m['spam_rate'], 2)}" for m in risky) + ".",
               "Rewrite or remove these messages first; check that the people entering the flow expect to hear "
               "from the brand.", severity="high" if any((m["spam_rate"] or 0) > SPAM_HIGH for m in risky) else "medium",
               category="deliverability", readout={"metric": "unsubscribe and spam rates of the fixed messages", "days": 30})
    bounced = sum(r["bounced"] for r in flows)
    if bounced >= 100:
        top_b = max(flows, key=lambda r: r["bounced"])
        share = a.fact("top_bounce_flow_share_365d", top_b["bounced"] / bounced, "ratio",
                       f"Share of flow bounces from {top_b['name']}", time_basis="send")
        if share >= 0.5:
            a.find("bounce_concentration", "One flow produces most bounces",
                   f"{top_b['name']} accounts for {pct(share, 0)} of flow bounces.",
                   f"{int(top_b['bounced']):,} of {int(bounced):,} bounces over 365 days.",
                   "Find which list or source feeds that flow and add address verification or double opt-in there.",
                   severity="medium", category="deliverability", facts=[f"{MODULE}.top_bounce_flow_share_365d"],
                   readout={"metric": "bounce rate of that flow", "days": 30})
