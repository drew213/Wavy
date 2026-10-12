"""Flows by lifecycle category for the monthly report: welcome, checkout, cart, browse, cross-sell,
replenishment (then winback, sunset and anything unclassified), each with its live flows, 365-day
recipients, attributed revenue and revenue per recipient.

A third-party twin (an identity-resolution tool such as Retention.com sending its own abandonment
flow from the account) is merged into the category it serves, so the row shows the whole category
and the twin's share of it. Flows with no recognisable category are kept in "other", never dropped.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from ..audit.snapshot import MissingData, Snapshot, ref
from .base import Analysis, pct, ratio, usd
from .flow_history import roll

MODULE = "flow_categories"
CATEGORIES = (("welcome", "Welcome"), ("checkout", "Checkout abandonment"), ("cart", "Cart abandonment"),
              ("browse", "Browse abandonment"), ("cross_sell", "Post-purchase & cross-sell"),
              ("replenishment", "Replenishment"), ("winback", "Winback"), ("sunset", "Sunset"), ("other", "Other flows"))
LABEL = dict(CATEGORIES)
OF_KIND = {"welcome": "welcome", "abandoned_checkout": "checkout", "abandoned_cart": "cart", "browse": "browse",
           "back_in_stock": "browse", "post_purchase": "cross_sell", "replenishment": "replenishment",
           "winback": "winback", "sunset": "sunset"}
BY_NAME = (("checkout", re.compile(r"checkout", re.I)), ("cart", re.compile(r"\bcart\b|abandon", re.I)),
           ("browse", re.compile(r"browse|viewed|back.?in.?stock", re.I)),
           ("welcome", re.compile(r"welcome|sign.?up|subscriber|new.?lead|pop.?up", re.I)),
           ("replenishment", re.compile(r"replenish|refill|reorder|restock", re.I)),
           ("winback", re.compile(r"win.?back|lapsed|re.?activation|re.?engage", re.I)),
           ("sunset", re.compile(r"sunset", re.I)),
           ("cross_sell", re.compile(r"post.?purchase|thank|cross.?sell|upsell|review|order.?follow", re.I)))
THIRD_PARTY = re.compile(r"retention(\.com)?|\brtn\b|reclaim|wunderkind|get.?emails|customers\.ai|opensend|"
                         r"black.?crow|identity|visitor.?id", re.I)


def category_of(kind: Optional[str], name: str) -> str:
    if kind in OF_KIND:
        return OF_KIND[kind]
    for cat, pattern in BY_NAME:
        if pattern.search(name or ""):
            return cat
    return "other"


def third_party(*texts: Optional[str]) -> bool:
    return any(THIRD_PARTY.search(t or "") for t in texts)


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    snap.require("api")
    flows = snap.flows()
    if not flows:
        raise MissingData("no flow definitions in the snapshot", ref("api", "flows", "details"))
    try:
        perf: Optional[dict] = roll(snap.report_results("flow365"), "flow_id", "flow_name")
    except MissingData:
        perf = None
        a.notes.append("flow revenue is unavailable: the 365-day flow report wasn't collected")
    rows: dict[str, list[dict]] = defaultdict(list)
    for f in flows:
        p = (perf or {}).get(str(f.id)) or {}
        rows[category_of(f.kind, f.name)].append({
            "id": f.id, "name": f.name, "status": f.status, "live": f.classified_live,
            "third_party": third_party(f.name, f.trigger_metric, f.trigger_integration),
            "recipients": p.get("recipients") if perf is not None else None,
            "revenue": p.get("conversion_value") if perf is not None else None, "rpr": p.get("rpr") if p else None})
    table = []
    for cat, label in CATEGORIES:
        members = sorted(rows.get(cat) or [], key=lambda r: (not r["live"], -(r["revenue"] or 0)))
        if not members and cat == "other":
            continue
        live = [r for r in members if r["live"]]
        a.fact(f"{cat}.live_flows", len(live), "flows", f"{label}: live flows", source=ref("api", "flows", "details"))
        row = {"category": cat, "label": label, "flows": members, "live_flows": len(live),
               "third_party": [r["name"] for r in members if r["third_party"]]}
        if perf is not None:
            revenue = sum(r["revenue"] or 0.0 for r in members)
            recipients = sum(r["recipients"] or 0.0 for r in members)
            twin = sum(r["revenue"] or 0.0 for r in members if r["third_party"])
            src = ref("perf", "reports", "flow365")
            row.update(revenue=a.fact(f"{cat}.revenue_365d", revenue, "USD", f"{label}: flow-attributed revenue, 365 days",
                                      window="365d", time_basis="send", source=src),
                       recipients=a.fact(f"{cat}.recipients_365d", recipients, "recipients",
                                         f"{label}: recipients, 365 days", window="365d", time_basis="send", source=src),
                       rpr=a.fact(f"{cat}.rpr_365d", ratio(revenue, recipients), "USD",
                                  f"{label}: revenue per recipient, 365 days", window="365d", time_basis="send", source=src))
            if row["third_party"]:
                row["third_party_revenue"] = a.fact(f"{cat}.third_party_revenue_365d", twin, "USD",
                                                    f"{label}: third-party flow revenue, 365 days", window="365d",
                                                    time_basis="send", source=src)
                row["third_party_share"] = a.fact(f"{cat}.third_party_share", ratio(twin, revenue), "ratio",
                                                  f"{label}: third-party share of category revenue", window="365d",
                                                  time_basis="send", source=src)
        table.append(row)
    a.table("categories", table)
    a.fact("categories_live", sum(1 for r in table if r["category"] != "other" and r["live_flows"]), "categories",
           f"Flow categories with a live flow (of {len(CATEGORIES) - 1})")
    _findings(a, table, perf is not None)


def _findings(a: Analysis, table: list[dict], measured: bool) -> None:
    for row in table:
        cat, label = row["category"], row["label"]
        live = [r for r in row["flows"] if r["live"]]
        native = [r for r in live if not r["third_party"]]
        twins = [r for r in live if r["third_party"]]
        if native and twins and cat != "other":
            names = "; ".join(r["name"] for r in native[:2] + twins[:2])
            evidence = f"Live in {label.lower()}: {names}."
            facts = [f"{MODULE}.{cat}.live_flows"]
            if measured and row.get("revenue"):
                evidence += (f" The third-party flow earned {usd(row.get('third_party_revenue'))} of the category's "
                             f"{usd(row['revenue'])} in 365 days ({pct(row.get('third_party_share'))}).")
                facts += [f"{MODULE}.{cat}.third_party_revenue_365d", f"{MODULE}.{cat}.revenue_365d",
                          f"{MODULE}.{cat}.third_party_share"]
            a.find(f"{cat}_twin", f"{label}: native and third-party flows both live",
                   f"The same {label.lower()} visitor can enter a Klaviyo flow and a third-party twin, so they may "
                   "get two sequences and the revenue is split between them.",
                   evidence,
                   "Set one suppression rule: anyone in the native flow is filtered out of the twin (or the twin only "
                   "catches visitors Klaviyo can't identify), then compare revenue per recipient after 30 days and keep "
                   "the better path first.",
                   severity="medium", category="flow_optimization", facts=facts, effort_days=1,
                   readout={"metric": f"{label.lower()} revenue per recipient", "days": 30,
                            "fact": f"{MODULE}.{cat}.rpr_365d"})
        if measured and cat != "other":
            silent = [r for r in live if (r["recipients"] or 0) > 0 and not r["revenue"]]
            if silent:
                a.find(f"{cat}_no_revenue", f"{label}: live flows earning nothing",
                       f"{len(silent)} live {label.lower()} flow(s) sent in the last 365 days without attributed revenue.",
                       "; ".join(f"{r['name']} ({r['recipients']:,.0f} recipients)" for r in silent[:4]) + ".",
                       "Open each flow: check the links resolve, the offer still exists and Placed Order is attributed "
                       "to it; rebuild the first message or turn the flow off if it duplicates another.",
                       severity="medium", category="flow_optimization", facts=[f"{MODULE}.{cat}.live_flows"], effort_days=1,
                       readout={"metric": f"{label.lower()} revenue", "days": 30, "fact": f"{MODULE}.{cat}.revenue_365d"})
