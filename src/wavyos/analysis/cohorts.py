"""First-observed buyer cohorts: repeat rate and value per buyer at 30/60/90/180/365 days.

Method rules: a cohort is reported at a horizon only once every buyer in it has had the full horizon
(mature horizons only); proportions carry a 95% Wilson interval; any group under 10 buyers is
suppressed; buyers in the first 90 days of the order window are left out because they may be returning
customers whose earlier orders fall outside it ("first observed", not "new").
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ..audit.snapshot import MissingData, Snapshot, ref
from .base import Analysis, num, pct, usd, wilson
from .series import BURN_IN_DAYS, orders_rows

MODULE = "cohorts"
HORIZONS = (30, 60, 90, 180, 365)
MIN_BUYERS = 10
HEADLINE_HORIZON = 90


def _profiles(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("p"):
            out[r["p"]].append(r)
    return out


def _measure(buyers: list[list[dict]], horizon: int) -> dict:
    n = len(buyers)
    repeat = sum(1 for orders in buyers if len(orders) > 1 and orders[1]["_t"] - orders[0]["_t"] <= timedelta(days=horizon))
    value = sum(num(o.get("v")) for orders in buyers for o in orders if o["_t"] - orders[0]["_t"] <= timedelta(days=horizon))
    lo, hi = wilson(repeat, n)
    return {"buyers": n, "repeaters": repeat, "repeat_rate": repeat / n if n else None, "ci_low": lo, "ci_high": hi,
            "value_per_buyer": value / n if n else None}


def run(snap: Snapshot, a: Analysis, context: dict) -> None:
    rows, end, start = orders_rows(snap)
    if not rows:
        raise MissingData("no stored orders", ref("orders", "orders"))
    origin = (start or rows[0]["_t"]) + timedelta(days=BURN_IN_DAYS)
    tz = snap.tz
    people = {p: o for p, o in _profiles(rows).items() if o[0]["_t"] >= origin}

    def mature(orders: list[dict], h: int) -> bool:
        return orders[0]["_t"] + timedelta(days=h) <= end

    by_month: dict[str, list[list[dict]]] = defaultdict(list)
    for orders in people.values():
        by_month[orders[0]["_t"].astimezone(tz).strftime("%Y-%m")].append(orders)
    table = []
    for month, buyers in sorted(by_month.items()):
        row = {"cohort": month, "buyers": len(buyers)}
        for h in HORIZONS:
            ripe = [b for b in buyers if mature(b, h)]
            if len(ripe) < len(buyers):
                row[f"d{h}"] = None  # not mature yet
            elif len(buyers) < MIN_BUYERS:
                row[f"d{h}"] = "suppressed"
            else:
                row[f"d{h}"] = _measure(buyers, h)
        table.append(row)
    a.table("monthly_cohorts", table)

    pooled = []
    for h in HORIZONS:
        ripe = [b for b in people.values() if mature(b, h)]
        if len(ripe) < MIN_BUYERS:
            a.fact(f"repeat_rate_d{h}", None, "ratio", f"Repeat rate within {h} days: fewer than {MIN_BUYERS} mature buyers")
            continue
        m = _measure(ripe, h)
        pooled.append({"horizon": h, **m})
        a.fact(f"repeat_rate_d{h}", m["repeat_rate"], "ratio", f"Share of first-observed buyers who ordered again within {h} days",
               window=f"buyers with {h} days of history", source=ref("orders", "orders"), evidence_class="documented")
        a.fact(f"repeat_rate_d{h}_ci", f"{pct(m['ci_low'])} to {pct(m['ci_high'])}", "interval",
               f"95% Wilson interval, repeat within {h} days")
        a.fact(f"value_per_buyer_d{h}", m["value_per_buyer"], "USD", f"Order value per first-observed buyer within {h} days")
        a.fact(f"buyers_d{h}", m["buyers"], "profiles", f"First-observed buyers with {h} days of history")
    a.table("pooled", pooled)

    # splits: first order discounted or not, and order source
    splits = []
    for split, key in (("discount", lambda o: "discounted" if o[0].get("d") else "full price"),
                       ("source", lambda o: str(o[0].get("s") or "(not set)").lower())):
        groups: dict[str, list] = defaultdict(list)
        for b in people.values():
            if mature(b, HEADLINE_HORIZON):
                groups[key(b)].append(b)
        for g, buyers in sorted(groups.items()):
            if len(buyers) < MIN_BUYERS:
                splits.append({"split": split, "group": g, "buyers": len(buyers), "suppressed": True})
            else:
                splits.append({"split": split, "group": g, **_measure(buyers, HEADLINE_HORIZON)})
    a.table(f"splits_d{HEADLINE_HORIZON}", splits)

    head = next((p for p in pooled if p["horizon"] == HEADLINE_HORIZON), None)
    disc = {s["group"]: s for s in splits if s["split"] == "discount" and not s.get("suppressed")}
    if head and {"discounted", "full price"} <= set(disc):
        d, f = disc["discounted"], disc["full price"]
        a.fact("repeat_d90_discounted", d["repeat_rate"], "ratio", "90-day repeat rate, first order used a discount code")
        a.fact("repeat_d90_full_price", f["repeat_rate"], "ratio", "90-day repeat rate, first order at full price")
        if d["ci_high"] < f["ci_low"]:
            a.find("discount_buyers_repeat_less", "Discount-acquired buyers come back less",
                   f"Buyers whose first order used a code repeat at {pct(d['repeat_rate'])} within 90 days, against "
                   f"{pct(f['repeat_rate'])} for full-price first orders; the intervals don't overlap.",
                   f"{d['buyers']:,} discounted and {f['buyers']:,} full-price first-observed buyers with 90 days of history.",
                   "Give discount-acquired buyers a value-led post-purchase path (product education, no second code), "
                   "and test a smaller or non-cash welcome offer.",
                   severity="medium", category="retention", lever="post_purchase",
                   facts=[f"{MODULE}.repeat_d90_discounted", f"{MODULE}.repeat_d90_full_price"],
                   readout={"metric": "90-day repeat rate of discount-acquired buyers", "days": 120})
    if head and head["repeat_rate"] is not None and head["repeat_rate"] < 0.12:
        a.find("low_early_repeat", "Few first-time buyers order again within 90 days",
               f"{pct(head['repeat_rate'])} of first-observed buyers ordered again within 90 days "
               f"(95% interval {pct(head['ci_low'])} to {pct(head['ci_high'])}).",
               f"{head['buyers']:,} buyers with 90 days of history; value per buyer {usd(head['value_per_buyer'])}.",
               "Build the second-order path: a post-purchase series timed before the measured reorder gap, with a "
               "cross-sell from what first-time buyers most often buy next.",
               severity="high", category="retention", lever="post_purchase", facts=[f"{MODULE}.repeat_rate_d90"],
               readout={"metric": "90-day repeat rate", "days": 120})
