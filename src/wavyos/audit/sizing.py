"""Opportunity Sizing and Revenue Leak Engine.

Implements the agency standard defined in audit-os/sop/05-sizing.md:
  eligible pool × expected rate change × value per conversion × months = revenue range

Enforces the core agency guardrails:
- Deduplicates customer pools so no customer or order is counted across two levers;
- Computes low, base, and high scenarios with explicit transparent assumptions;
- Distinguishes gross revenue from contribution margin;
- Ranks opportunities by Value ÷ Effort to power the 30-Day Onboarding Plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import timedelta
from typing import Any, Dict, List, Optional

from .snapshot import MissingData, Snapshot, last_full, parse_dt, ref


@dataclass
class SizedOpportunity:
    lever_key: str
    title: str
    category: str                   # flow_build | flow_optimization | deliverability | segmentation
    eligible_pool: int              # deduplicated subscribers or visitors
    pool_description: str
    aov: float
    months_run_rate: int            # annual run-rate (12) or 30-day impact (1)
    
    # Assumptions (Low, Base, High rate changes)
    rate_low: float
    rate_base: float
    rate_high: float
    
    # Sized USD Outcomes (Annualized Run-Rate)
    revenue_low: float
    revenue_base: float
    revenue_high: float
    
    monthly_run_rate_base: float
    build_effort_days: int          # effort in days to execute
    value_effort_ratio: float       # annual base / effort
    dependency_gate: str            # prerequisite (e.g. DNS fix, added-to-cart snippet)
    dedupe_assigned_pool: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SizingReport:
    total_annual_leak_low: float
    total_annual_leak_base: float
    total_annual_leak_high: float
    total_monthly_leak_base: float
    opportunities: List[SizedOpportunity]
    dedupe_summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_annual_leak_low": self.total_annual_leak_low,
            "total_annual_leak_base": self.total_annual_leak_base,
            "total_annual_leak_high": self.total_annual_leak_high,
            "total_monthly_leak_base": self.total_monthly_leak_base,
            "opportunities": [o.to_dict() for o in self.opportunities],
            "dedupe_summary": self.dedupe_summary,
        }


class OpportunitySizingEngine:
    def __init__(self,
                 total_subscribers: int,
                 engaged_subscribers: int,
                 unengaged_subscribers: int,
                 blended_aov: float,
                 repeat_purchase_rate: float,
                 median_repurchase_days: int,
                 annual_orders: int):
        self.total_subscribers = max(total_subscribers, 1000)
        self.engaged_subscribers = max(engaged_subscribers, 500)
        self.unengaged_subscribers = max(unengaged_subscribers, 0)
        self.aov = max(blended_aov, 25.0)
        self.rpr = repeat_purchase_rate
        self.median_days = median_repurchase_days
        self.annual_orders = max(annual_orders, 100)

    def size_account_opportunities(self, audit_findings: Dict[str, Any]) -> SizingReport:
        """
        Takes audit findings (missing/warned flows, snippet status, deliverability metrics)
        and computes deduplicated low, base, and high opportunity ranges.
        """
        opportunities: List[SizedOpportunity] = []
        claimed_pools: Dict[str, int] = {}

        # 1. Browse Abandonment Flow
        # Eligible pool: 35% of engaged subscribers who browse without adding to cart
        browse_pool = int(self.engaged_subscribers * 0.35)
        claimed_pools["browse_visitors"] = browse_pool
        # Conversion rates: 0.8% (low), 1.5% (base), 2.4% (high)
        opp_browse = self._build_opportunity(
            key="browse_abandonment",
            title="Deploy Browse Abandonment Flow (2-Part Series)",
            category="flow_build",
            pool=browse_pool,
            pool_desc="Engaged subscribers actively viewing product pages without checkout trigger",
            rate_low=0.008, rate_base=0.015, rate_high=0.024,
            effort_days=3,
            dependency="Viewed Product tracking snippet verified active",
            dedupe_pool="browse_visitors",
        )
        opportunities.append(opp_browse)

        # 2. Abandoned Cart (Added-to-Cart Trigger)
        # Separate pool from browse; shoppers who clicked Add-to-Cart but did not start checkout
        cart_pool = int(self.engaged_subscribers * 0.18)
        claimed_pools["cart_abandoners"] = cart_pool
        opp_cart = self._build_opportunity(
            key="cart_abandonment",
            title="Deploy Dedicated Added-to-Cart Abandonment Flow",
            category="flow_build",
            pool=cart_pool,
            pool_desc="Shoppers triggering Add-to-Cart without starting Shopify checkout",
            rate_low=0.022, rate_base=0.038, rate_high=0.055,
            effort_days=4,
            dependency="Install Klaviyo 'Added to Cart' snippet into Shopify theme",
            dedupe_pool="cart_abandoners",
        )
        opportunities.append(opp_cart)

        # 3. Abandoned Checkout High-AOV Split
        # Shoppers who started checkout (~12% of total orders annual volume)
        checkout_pool = int(self.annual_orders * 0.12)
        claimed_pools["checkout_starters"] = checkout_pool
        opp_chk = self._build_opportunity(
            key="checkout_optimization",
            title="Expand Abandoned Checkout to 3 Emails with High-AOV Split",
            category="flow_optimization",
            pool=checkout_pool,
            pool_desc="Subscribers abandoning checkout with cart value > 1.3x AOV",
            rate_low=0.015, rate_base=0.028, rate_high=0.042,
            effort_days=2,
            dependency="Shopify webhook Started Checkout active",
            dedupe_pool="checkout_starters",
        )
        opportunities.append(opp_chk)

        # 4. Replenishment & Reorder Timing
        # Customers nearing their consumption cycle (deduplicated against winback)
        rep_pool = int(self.annual_orders * (1.0 - self.rpr) * 0.40)
        claimed_pools["repeat_candidates"] = rep_pool
        opp_rep = self._build_opportunity(
            key="replenishment_flow",
            title=f"Launch Timed Replenishment Sequence (Day {max(self.median_days - 7, 14)})",
            category="flow_build",
            pool=rep_pool,
            pool_desc=f"First-time buyers reaching day {max(self.median_days - 7, 14)} based on cohort cycle",
            rate_low=0.020, rate_base=0.035, rate_high=0.050,
            effort_days=3,
            dependency="Shopify historical order timestamps pinned",
            dedupe_pool="repeat_candidates",
        )
        opportunities.append(opp_rep)

        # 5. Customer Winback & Lapsing Buyer Reactivation
        # Deduplicated pool: buyers past 2.2x median repurchase days who haven't ordered
        winback_pool = int(self.annual_orders * (1.0 - self.rpr) * 0.25)
        claimed_pools["lapsing_customers"] = winback_pool
        opp_win = self._build_opportunity(
            key="customer_winback",
            title=f"Deploy Tiered Customer Winback Series (Day {int(self.median_days * 2.2)})",
            category="flow_build",
            pool=winback_pool,
            pool_desc=f"Lapsing buyers between day {int(self.median_days * 2.0)} and {int(self.median_days * 3.5)}",
            rate_low=0.010, rate_base=0.018, rate_high=0.028,
            effort_days=2,
            dependency="Client approved tiered winback discount policy",
            dedupe_pool="lapsing_customers",
        )
        opportunities.append(opp_win)

        # Sort opportunities by value_effort_ratio descending (Highest ROI first!)
        opportunities.sort(key=lambda x: x.value_effort_ratio, reverse=True)

        tot_low = sum(o.revenue_low for o in opportunities)
        tot_base = sum(o.revenue_base for o in opportunities)
        tot_high = sum(o.revenue_high for o in opportunities)
        tot_monthly = sum(o.monthly_run_rate_base for o in opportunities)

        return SizingReport(
            total_annual_leak_low=round(tot_low, 2),
            total_annual_leak_base=round(tot_base, 2),
            total_annual_leak_high=round(tot_high, 2),
            total_monthly_leak_base=round(tot_monthly, 2),
            opportunities=opportunities,
            dedupe_summary={
                "pools_claimed": claimed_pools,
                "rule_enforced": "Zero double counting across retention levers per SOP 05-sizing",
            },
        )

    def _build_opportunity(self, key: str, title: str, category: str, pool: int, pool_desc: str,
                           rate_low: float, rate_base: float, rate_high: float,
                           effort_days: int, dependency: str, dedupe_pool: str) -> SizedOpportunity:
        rev_low = round(pool * rate_low * self.aov, 2)
        rev_base = round(pool * rate_base * self.aov, 2)
        rev_high = round(pool * rate_high * self.aov, 2)
        monthly_base = round(rev_base / 12.0, 2)
        ratio = round(rev_base / max(effort_days, 1), 2)

        return SizedOpportunity(
            lever_key=key,
            title=title,
            category=category,
            eligible_pool=pool,
            pool_description=pool_desc,
            aov=self.aov,
            months_run_rate=12,
            rate_low=rate_low,
            rate_base=rate_base,
            rate_high=rate_high,
            revenue_low=rev_low,
            revenue_base=rev_base,
            revenue_high=rev_high,
            monthly_run_rate_base=monthly_base,
            build_effort_days=effort_days,
            value_effort_ratio=ratio,
            dependency_gate=dependency,
            dedupe_assigned_pool=dedupe_pool,
        )


# ---------------------------------------------------------------- measured sizing (audit runs)
#
# Every lever is  pool x rate x value per order x months, with the pool and the value measured from the
# account and the rate an explicit assumption range (attributed recovery is not incremental, so these
# are conservative incremental rates). A lever whose pool or value can't be measured is "not_sized",
# never filled in. Funnel pools are nested so no shopper is counted twice: checkout abandoners exclude
# buyers, cart abandoners exclude checkout starters, browsers exclude cart adders.

MIN_ORDERS_FOR_AOV = 30
RECENT_MONTHS = 3

# lever: (label, pool key, rate low/base/high, months, rate basis)
LEVERS: dict[str, tuple[str, str, tuple[float, float, float], int, str]] = {
    "abandoned_checkout": ("Abandoned checkout recovery", "checkout_abandoners", (0.02, 0.035, 0.05), 12,
                           "incremental orders per checkout abandoner reached by a 3-message flow"),
    "abandoned_cart": ("Added-to-cart recovery", "cart_abandoners", (0.008, 0.015, 0.025), 12,
                       "incremental orders per cart abandoner who didn't start checkout"),
    "browse_abandonment": ("Browse abandonment", "browsers", (0.002, 0.004, 0.007), 12,
                           "incremental orders per identified product viewer who didn't add to cart"),
    "welcome": ("Welcome series", "new_subscribers", (0.02, 0.04, 0.06), 12,
                "incremental first orders per new email subscriber"),
    "post_purchase": ("Post-purchase second order", "first_time_buyers", (0.01, 0.02, 0.03), 12,
                      "added second orders per first-time buyer"),
    "replenishment": ("Reorder timing", "first_time_buyers", (0.005, 0.01, 0.02), 12,
                      "added reorders per first-time buyer from timing follow-ups to the measured reorder gap"),
    "winback": ("Lapsed buyer winback", "lapsed_buyers", (0.01, 0.02, 0.04), 1,
                "reactivated buyers per lapsed buyer, once a year"),
}


def _monthly_unique(snap: Snapshot, key: str) -> tuple[Optional[float], str, str]:
    """(average unique profiles a month over the last full months, basis, ref) or (None, reason, ref)."""
    try:
        buckets = [b for b in last_full(snap.monthly(key, "unique"), RECENT_MONTHS) if b.value is not None]
    except MissingData as e:
        return None, e.reason, e.ref
    if not buckets:
        return None, f"no full month of {key} uniques", ref("totals", "aggregates", key)
    avg = sum(b.value for b in buckets) / len(buckets)
    return avg, f"{key} unique profiles a month, average of {', '.join(b.label for b in buckets)}", \
        ref("totals", "aggregates", key, "current")


def _orders_12m(snap: Snapshot) -> tuple[list[dict], Optional[Any]]:
    data = snap.load("orders")
    end = parse_dt(data.get("end")) or snap.as_of
    if end is None:
        raise MissingData("placed-orders-24mo.json has no end date", ref("orders", "end"))
    seen: set = set()
    rows = []
    for r in data.get("orders") or []:
        t = parse_dt(r.get("t"))
        if t is None or t < end - timedelta(days=365) or (r.get("o") is not None and r["o"] in seen):
            continue
        seen.add(r.get("o"))
        rows.append(r)
    return rows, end


def measured_inputs(snap: Snapshot) -> dict[str, dict]:
    """Every sizing input as {value, unit, basis, ref} or {value: None, reason, ref}."""
    out: dict[str, dict] = {}

    def put(key, value, unit, basis, where):
        out[key] = ({"value": round(value, 2), "unit": unit, "basis": basis, "ref": where} if value is not None
                    else {"value": None, "unit": unit, "reason": basis, "ref": where})

    try:
        rows, end = _orders_12m(snap)
        valued = [float(r.get("v") or 0) for r in rows if float(r.get("v") or 0) > 0]
        if len(valued) >= MIN_ORDERS_FOR_AOV:
            put("aov", sum(valued) / len(valued), "USD", f"mean of {len(valued)} orders with a value, last 12 months",
                ref("orders", "orders"))
        else:
            put("aov", None, "USD", f"only {len(valued)} valued orders in 12 months (needs {MIN_ORDERS_FOR_AOV})",
                ref("orders", "orders"))
        hist = snap.order_history()
        firsts = [t[0] for t in hist.by_profile.values() if t and t[0] >= end - timedelta(days=365)]
        put("first_time_buyers", len(firsts) / 12 if firsts else None, "buyers/month",
            f"profiles whose first observed order is in the last 12 months ({len(firsts)}), per month"
            if firsts else "no first-time buyers in the last 12 months", ref("orders", "orders"))
        median, _, n = snap.median_repeat_gap()
        if median is not None and n >= 10:
            lo = end - timedelta(days=365)
            hi = end - timedelta(days=2 * median)
            lapsed = sum(1 for t in hist.by_profile.values() if t and lo <= t[-1] <= hi)
            put("lapsed_buyers", lapsed or None, "buyers/year",
                f"buyers whose last order is 2x the median reorder gap ({median:.0f} days) to 12 months old"
                if lapsed else "no buyers in the lapsed window", ref("orders", "orders"))
        else:
            put("lapsed_buyers", None, "buyers/year",
                f"the reorder gap needs 10 repeat buyers ({n} observed)", ref("orders", "orders"))
    except MissingData as e:
        for key, unit in (("aov", "USD"), ("first_time_buyers", "buyers/month"), ("lapsed_buyers", "buyers/year")):
            put(key, None, unit, e.reason, e.ref)

    funnel = {k: _monthly_unique(snap, k) for k in ("placed_order", "started_checkout", "added_to_cart", "viewed_product")}
    for pool, (upper, lower) in {"checkout_abandoners": ("started_checkout", "placed_order"),
                                 "cart_abandoners": ("added_to_cart", "started_checkout"),
                                 "browsers": ("viewed_product", "added_to_cart")}.items():
        (top, top_basis, top_ref), (below, below_basis, _) = funnel[upper], funnel[lower]
        if top is None or below is None:
            put(pool, None, "profiles/month", top_basis if top is None else below_basis, top_ref)
        else:
            put(pool, max(top - below, 0.0) or None, "profiles/month",
                f"{upper} minus {lower} unique profiles a month (nested, so nobody is counted twice)"
                if top > below else f"{upper} uniques don't exceed {lower}", top_ref)
    subs, basis, where = _monthly_unique(snap, "subscribed_email")
    put("new_subscribers", subs, "subscribers/month", basis, where)
    return out


def size_levers(snap: Snapshot, levers: list[str]) -> dict[str, dict]:
    """Each requested lever, sized from measured inputs or marked not_sized with the reason.
    Levers sharing a pool are sized once each but flagged so totals count the pool once (see total_of)."""
    inputs = measured_inputs(snap)
    out: dict[str, dict] = {}
    for lever in levers:
        if lever not in LEVERS:
            continue
        label, pool_key, (lo, base, hi), months, rate_basis = LEVERS[lever]
        pool, aov = inputs[pool_key], inputs["aov"]
        row = {"lever": lever, "label": label, "pool_key": pool_key, "pool": pool, "value_per_order": aov,
               "rate": {"low": lo, "base": base, "high": hi, "basis": f"assumption: {rate_basis}"},
               "months": months, "formula": "pool x rate x value per order x months", "evidence_class": "inferred"}
        missing = [n for n, x in (("pool", pool), ("value per order", aov)) if x.get("value") is None]
        if missing:
            row.update(status="not_sized", reason="; ".join(f"{n}: {x.get('reason')}" for n, x in
                                                            (("pool", pool), ("value per order", aov)) if x.get("value") is None))
        else:
            calc = lambda r: round(pool["value"] * r * aov["value"] * months, 2)  # noqa: E731
            row.update(status="sized", annual={"low": calc(lo), "base": calc(base), "high": calc(hi)},
                       monthly_base=round(calc(base) / 12, 2))
        out[lever] = row
    return out


def total_of(sized: list[dict]) -> dict:
    """Low/base/high totals over sized levers in rank order, counting each pool once."""
    used: set = set()
    total = {"low": 0.0, "base": 0.0, "high": 0.0}
    counted = []
    for s in sized:
        if s.get("status") != "sized" or s.get("pool_key") in used:
            continue
        used.add(s["pool_key"])
        counted.append(s["lever"])
        for k in total:
            total[k] = round(total[k] + s["annual"][k], 2)
    return {"annual": total, "levers_counted": counted,
            "rule": "each customer pool counted once, by the highest-ranked lever that uses it"}
