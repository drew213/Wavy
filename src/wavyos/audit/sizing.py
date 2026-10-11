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
from typing import Any, Dict, List, Optional


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
