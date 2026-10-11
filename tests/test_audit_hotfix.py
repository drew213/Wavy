"""Regressions from a WooCommerce store's first audit: a dormant Shopify metric, a >1y window, a 1-part score."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from wavyos import klaviyo_intake
from wavyos.collector.metrics import pick_metric
from wavyos.collector.stages import order_key

SHOP = {"id": "SHOP", "name": "Placed Order", "integration": {"key": "shopify", "name": "Shopify"}}
WOO = {"id": "WOO", "name": "Placed Order", "integration": {"key": "woocommerce", "name": "WooCommerce"}}
API = {"id": "API", "name": "Placed Order", "integration": {"key": "api", "name": "API"}}


def test_a_dormant_shopify_copy_loses_to_the_live_woocommerce_metric():
    picked = pick_metric([SHOP, WOO], "Placed Order", "ecommerce", activity={"SHOP": 0, "WOO": 412})
    assert picked["status"] == "selected" and picked["metric"]["id"] == "WOO"
    assert "dormant" in picked["reason"]


def test_unknown_activity_is_never_treated_as_dormant():
    picked = pick_metric([SHOP, WOO], "Placed Order", "ecommerce", activity={"SHOP": None, "WOO": None})
    assert picked["status"] == "ambiguous"
    by_platform = pick_metric([SHOP, WOO], "Placed Order", "ecommerce",
                              activity={"SHOP": None, "WOO": None}, platform="WooCommerce")
    assert by_platform["metric"]["id"] == "WOO" and "store platform" in by_platform["reason"]


def test_without_activity_or_platform_shopify_is_still_preferred():
    assert pick_metric([SHOP, WOO], "Placed Order", "ecommerce")["metric"]["id"] == "SHOP"
    assert pick_metric([SHOP, API], "Placed Order", "ecommerce", activity={"SHOP": 3})["metric"]["id"] == "SHOP"


def test_the_totals_window_stays_under_klaviyos_one_year_limit():
    for now in (datetime(2026, 10, 11, 21, 0, tzinfo=timezone.utc), datetime(2026, 3, 31, 23, 59, tzinfo=timezone.utc)):
        start, end = klaviyo_intake.totals_window(now, "America/New_York")
        span = datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))
        assert span.days < 365, (start, end)


def test_the_health_score_is_withheld_until_three_parts_are_measured():
    one = klaviyo_intake.health({"coverage": {"welcome": {"live": True}}}, None)
    assert one["score"] is None and "1 of 3" in one["withheld"]
    k = {"coverage": {"welcome": {"live": True}}, "revenue_12m": {"email_share": 0.2}}
    full = klaviyo_intake.health(k, 0.25)
    assert full["score"] is not None and full["withheld"] is None


def test_reused_order_numbers_stay_separate_orders():
    a = order_key("woocommerce", "1001", "2024-01-02T10:00:00Z", 54.0)
    b = order_key("woocommerce", "1001", "2025-06-09T15:30:00Z", 88.5)
    assert a != b and a.startswith("1001~")
    assert a == order_key("woocommerce", "1001", "2024-01-02T10:00:00Z", 54.0)


BENCHMARK = re.compile(r"\b(42500|42_500|14200|14_200|11500|11_500|34200|34_200)\b|0\.281\b")


def test_benchmark_numbers_live_only_in_the_benchmark_module():
    src = Path(__file__).resolve().parents[1] / "src" / "wavyos"
    hits = [f"{p.relative_to(src)}:{i}" for p in src.rglob("*") if p.suffix in {".py", ".html", ".js"}
            and p.name != "benchmarks.py" for i, line in enumerate(p.read_text().splitlines(), 1)
            if BENCHMARK.search(line)]
    assert hits == [], hits
