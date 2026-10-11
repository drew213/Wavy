"""Benchmark stand-ins, used only when a brand chose "Run on benchmark data" (no Klaviyo key).

Every figure here describes a fictional $2.5-3M store. A run that has a key never reads this module:
a value it can't measure is shown as unavailable with the reason, not replaced by these. Pages label
anything sourced from here as benchmark data.
"""
from __future__ import annotations

from .connectors.shopify import ShopifyCohortData

POOLS = {"total_subscribers": 42500, "engaged_subscribers": 14200, "unengaged_subscribers": 11500}


def sample_cohort(store_domain: str) -> ShopifyCohortData:
    return ShopifyCohortData(
        store_domain=store_domain,
        currency="USD",
        total_orders_12m=34200,
        total_unique_customers_12m=26100,
        lifetime_gmv_12m=2736000.00,
        blended_aov=80.00,
        repeat_customer_count=7330,
        repeat_purchase_rate=0.281,
        median_days_to_second_order=36,
        top_gateway_skus=[
            {"name": "Barrier Recovery Complex (50ml)", "units": 12400},
            {"name": "Ultra Radiance C+ Serum", "units": 8900},
            {"name": "Trial Discovery Protocol 3-Pack", "units": 4800},
        ],
        top_repeat_skus=[
            {"name": "Barrier Recovery Complex (50ml)", "units": 6900},
            {"name": "Ultra Radiance C+ Serum", "units": 5200},
            {"name": "Hydra-Mist Infusion Refill", "units": 2400},
        ],
        refund_rate_pct=1.75,
    )
