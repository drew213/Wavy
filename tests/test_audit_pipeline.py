"""Phase 1: measured sizing, playbook opportunities and the metric picker's share rule (synthetic data only)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from wavyos.audit import opportunities
from wavyos.audit.library import specs as library_specs
from wavyos.audit.sizing import measured_inputs, size_levers, total_of
from wavyos.audit.snapshot import Snapshot
from wavyos.collector.metrics import pick_metric

END = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
MONTHS = [f"{y}-{mo:02d}-01T00:00:00+00:00" for y, mo in [(2025, m) for m in range(11, 13)] + [(2026, m) for m in range(1, 11)]]
UNIQUES = {"placed_order": 100, "started_checkout": 400, "added_to_cart": 1000, "viewed_product": 5000,
           "subscribed_email": 300}


def _agg(value):
    return {"current": {"dates": MONTHS, "rows": [{"dimensions": [], "measurements": {"unique": [value] * 12,
                                                                                       "count": [value] * 12}}]}}


def snapshot(n_orders=60, aggregates=UNIQUES, value=50.0):
    orders = []
    for i in range(n_orders):  # 40 buyers; the first 20 come back once, 60 days later
        p = f"P{i % 40:02d}"
        t = END - timedelta(days=300 - (i % 40) * 5 - (60 if i >= 40 else 0))
        orders.append({"p": p, "t": t.isoformat().replace("+00:00", "Z"), "v": value, "i": [], "o": f"o{i}"})
    totals = {"capturedAt": END.isoformat(), "account": {"timezone": "UTC"},
              "windows": {"current": {"start": "2025-11-01T00:00:00", "end": END.isoformat()}},
              "aggregates": {k: _agg(v) for k, v in aggregates.items()}}
    return Snapshot.from_dicts({"totals": totals, "orders": {"start": (END - timedelta(days=730)).isoformat(),
                                                             "end": END.isoformat(), "orders": orders}})


def test_funnel_pools_are_nested_and_measured():
    inputs = measured_inputs(snapshot())
    assert inputs["aov"]["value"] == 50.0
    assert inputs["checkout_abandoners"]["value"] == 300 and inputs["cart_abandoners"]["value"] == 600
    assert inputs["browsers"]["value"] == 4000 and inputs["new_subscribers"]["value"] == 300
    assert "minus" in inputs["cart_abandoners"]["basis"]


def test_a_sized_lever_shows_its_formula_and_assumption():
    lever = size_levers(snapshot(), ["abandoned_checkout"])["abandoned_checkout"]
    assert lever["status"] == "sized"
    assert lever["annual"]["base"] == pytest.approx(300 * 0.035 * 50 * 12)
    assert lever["rate"]["basis"].startswith("assumption") and lever["evidence_class"] == "inferred"


def test_a_lever_with_an_unmeasured_input_is_not_sized():
    few = size_levers(snapshot(n_orders=10), ["abandoned_checkout"])["abandoned_checkout"]
    assert few["status"] == "not_sized" and "valued orders" in few["reason"] and "annual" not in few
    no_views = {k: v for k, v in UNIQUES.items() if k != "viewed_product"}
    browse = size_levers(snapshot(aggregates=no_views), ["browse_abandonment"])["browse_abandonment"]
    assert browse["status"] == "not_sized" and "viewed_product" in browse["reason"]


def test_totals_count_each_customer_pool_once():
    sized = size_levers(snapshot(), ["post_purchase", "replenishment", "abandoned_checkout"])
    total = total_of([sized["post_purchase"], sized["replenishment"], sized["abandoned_checkout"]])
    assert total["levers_counted"] == ["post_purchase", "abandoned_checkout"]
    assert total["annual"]["base"] == pytest.approx(sized["post_purchase"]["annual"]["base"]
                                                    + sized["abandoned_checkout"]["annual"]["base"])


def test_failed_checks_become_ranked_proposed_cards():
    results = {
        "FLW-02": {"verdict": "fail", "message": "no live abandoned checkout flow", "evidence": []},
        "DAT-01": {"verdict": "warn", "message": "a dormant Placed Order metric is still in the account", "evidence": []},
        "DLV-02": {"verdict": "warn", "message": "bounce rate 1.2%", "evidence": []},
        "FLW-01": {"verdict": "pass", "message": "welcome live", "evidence": []},
        "RET-03": {"verdict": "na", "message": "no subscription metrics in the account", "evidence": []},
    }
    out = opportunities.build(snapshot(), results, library_specs())
    cards = out["opportunities"]
    assert [c["check_id"] for c in cards] == ["DAT-01", "FLW-02", "DLV-02"]
    assert all(c["status"] == "Proposed" and c["owner"] == "UNCONFIRMED" for c in cards)
    assert cards[1]["sizing"]["status"] == "sized" and cards[2]["sizing"] is None
    assert out["total"]["levers_counted"] == ["abandoned_checkout"]
    assert [g["check_id"] for g in out["gaps"]] == ["RET-03"] and out["gaps"][0]["reason"]


def test_every_playbook_names_a_real_check_and_lever():
    book = opportunities.playbooks()["playbooks"]
    from wavyos.audit.sizing import LEVERS
    assert set(book) == set(library_specs()), "every check needs a playbook so a failure is always actionable"
    assert {p["lever"] for p in book.values() if p["lever"]} <= set(LEVERS)
    assert all(p["steps"] and p["readout"]["metric"] for p in book.values())


SHOP = {"id": "SHOP", "name": "Placed Order", "integration": {"key": "shopify", "name": "Shopify"}}
WOO = {"id": "WOO", "name": "Placed Order", "integration": {"key": "woocommerce", "name": "WooCommerce"}}


def test_a_dominant_live_source_wins_and_a_real_split_stays_ambiguous():
    assert pick_metric([SHOP, WOO], "Placed Order", "ecommerce", activity={"SHOP": 30, "WOO": 900})["metric"]["id"] == "WOO"
    split = pick_metric([SHOP, WOO], "Placed Order", "ecommerce", activity={"SHOP": 400, "WOO": 600})
    assert split["status"] == "ambiguous"
