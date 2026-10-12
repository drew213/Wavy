"""Store vs attributed revenue by month and quarter, flow vs campaign split, refunds and order sources.

Placed Order can include orders email never touches (TikTok Shop, POS, marketplaces), so the source split
comes first and email's share is also reported against web orders alone.
"""
from __future__ import annotations

from collections import defaultdict

from ..audit.snapshot import MissingData, Snapshot, ref
from .base import Analysis, num, pct, ratio, stats_of, usd
from .series import orders_rows, quarter, try_monthly

MODULE = "performance"
WEB_SOURCES = {"", "web", "online_store", "online store", "checkout", "woocommerce", "shopify", "storefront", "api"}
OFF_SITE_SHARE = 0.15
REFUND_WARN = 0.05


def _report_split(snap: Snapshot, report: str) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in snap.report_results(report):
        out[(r.get("groupings") or {}).get("send_channel") or "email"] += num(stats_of(r).get("conversion_value"))
    return dict(out)


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    gross = try_monthly(snap, "placed_order", "sum_value")
    if gross is None:
        raise MissingData("no Placed Order revenue by month", ref("totals", "aggregates", "placed_order"))
    email = try_monthly(snap, "placed_order_by_channel", "sum_value", dimension="$email_channel") or {}
    sms = try_monthly(snap, "placed_order_by_channel", "sum_value", dimension="$sms_channel") or {}
    refunds = try_monthly(snap, "refunded_order", "sum_value")
    rows, quarters = [], defaultdict(lambda: defaultdict(float))
    for mo in sorted(gross):
        row = {"month": mo, "store": round(gross[mo], 2), "email": round(email.get(mo, 0.0), 2),
               "sms": round(sms.get(mo, 0.0), 2), "refunds": round(refunds.get(mo, 0.0), 2) if refunds else None}
        row["owned_share"] = ratio(row["email"] + row["sms"], row["store"])
        rows.append(row)
        for k in ("store", "email", "sms"):
            quarters[quarter(mo)][k] += row[k]
    a.table("monthly", rows)
    a.table("quarterly", [{"quarter": q, **{k: round(v, 2) for k, v in d.items()},
                           "owned_share": ratio(d["email"] + d["sms"], d["store"])} for q, d in sorted(quarters.items())])
    store_total = sum(gross.values())
    a.fact("store_revenue_full_months", store_total, "USD", "Store revenue, full months in the last 12",
           source=ref("totals", "aggregates", "placed_order"))

    # flows vs campaigns, 365 and 90 days
    split = {}
    for kind in ("flow", "campaign"):
        for days in (365, 90):
            try:
                by = _report_split(snap, f"{kind}{days}")
            except MissingData:
                continue
            split[(kind, days)] = by
            a.fact(f"{kind}_revenue_{days}d", sum(by.values()), "USD", f"{kind.title()}-attributed revenue, last {days} days",
                   window=f"last_{days}_days", time_basis="send", source=ref("perf", "reports", f"{kind}{days}"))
    if ("flow", 365) in split and ("campaign", 365) in split:
        f, c = sum(split[("flow", 365)].values()), sum(split[("campaign", 365)].values())
        a.fact("flow_share_365d", ratio(f, f + c), "ratio", "Flow share of email/SMS-attributed revenue, 365 days",
               window="last_365_days", time_basis="send")
        a.table("channel_split", [{"kind": k, "days": d, "channel": ch, "revenue": round(v, 2)}
                                  for (k, d), by in split.items() for ch, v in by.items()])

    if refunds:
        rate = a.fact("refund_rate_12m", ratio(sum(refunds.values()), store_total), "ratio",
                      "Refunded value as a share of store revenue, full months", source=ref("totals", "aggregates", "refunded_order"))
        if rate is not None and rate >= REFUND_WARN:
            a.find("refunds_high", "Refunds take a large bite of revenue",
                   f"Refunds equal {pct(rate)} of store revenue over the last 12 full months.",
                   f"{usd(sum(refunds.values()))} refunded against {usd(store_total)} in orders.",
                   "Find the products behind the refunds with the brand, then add how-to-use and expectation-setting "
                   "messages to the post-purchase flow for those products.",
                   severity="high" if rate >= 0.10 else "medium", category="retention", facts=[f"{MODULE}.refund_rate_12m"],
                   readout={"metric": "refund rate", "days": 90})
    else:
        a.fact("refund_rate_12m", None, "ratio", "Refund rate: no Refunded Order metric in the account")

    try:
        orders, _, _ = orders_rows(snap, days=365)
    except MissingData as e:
        a.notes.append(f"order sources unavailable: {e.reason}")
        return
    by_source: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for r in orders:
        s = str(r.get("s") or "").strip().lower()
        by_source[s][0] += 1
        by_source[s][1] += num(r.get("v"))
    total_n = sum(v[0] for v in by_source.values())
    a.table("sources", sorted([{"source": s or "(not set)", "orders": n, "revenue": round(v, 2),
                                "share": ratio(n, total_n)} for s, (n, v) in by_source.items()], key=lambda r: -r["orders"]))
    off = {s: v for s, v in by_source.items() if s not in WEB_SOURCES}
    off_n = sum(v[0] for v in off.values())
    off_share = a.fact("off_site_order_share_12m", ratio(off_n, total_n), "ratio",
                       "Share of last-12-month orders from sources other than the web store", source=ref("orders", "orders"))
    if off_share is not None and off_share >= OFF_SITE_SHARE:
        top = max(off, key=lambda s: off[s][0])
        web_rev = sum(v[1] for s, v in by_source.items() if s in WEB_SOURCES)
        email_12 = sum(email.values()) + sum(sms.values())
        web_share = a.fact("owned_share_of_web_12m", ratio(email_12, web_rev), "ratio",
                           "Email + SMS attributed revenue as a share of web-store order value", source=ref("orders", "orders"))
        a.find("off_site_orders", "A large share of orders don't come through the web store",
               f"{pct(off_share, 0)} of the last 12 months' orders came from sources email can't reach the same way, "
               f"led by '{top}'.",
               f"{off_n:,} of {total_n:,} orders; against web orders alone email and SMS earn {pct(web_share)}.",
               f"Judge email against web orders, and build a capture path for '{top}' buyers (insert card, "
               "post-purchase SMS or email opt-in) so they enter the retention program.",
               severity="medium", category="measurement",
               facts=[f"{MODULE}.off_site_order_share_12m", f"{MODULE}.owned_share_of_web_12m"],
               readout={"metric": f"'{top}' buyers captured into email or SMS", "days": 60})
