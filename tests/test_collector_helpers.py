"""The collector's ported helpers: metric selection and pins, DST-safe boundaries, order item titles.

Boundary and order-item cases are the Node collector's own tests (reference/klaviyo-audit/*.test.js).
Metric inventories are synthetic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfoNotFoundError

import pytest

from wavyos.collector.boundaries import boundary_to_utc, month_start, window_to_utc
from wavyos.collector.metrics import PinError, describe, pick_metric
from wavyos.collector.order_items import item_names


def metric(mid, name, integ):
    return {"id": mid, "name": name, "integration": {"name": integ, "key": integ.lower(), "category": ""}}


# ---------------------------------------------------------------- boundaries

@pytest.mark.parametrize("zone,summer,winter", [("America/New_York", "04", "05"), ("America/Chicago", "05", "06")])
def test_local_boundaries_use_summer_and_winter_offsets(zone, summer, winter):
    assert boundary_to_utc("2026-07-01T00:00:00", zone) == f"2026-07-01T{summer}:00:00.000Z"
    assert boundary_to_utc("2026-01-01T00:00:00", zone) == f"2026-01-01T{winter}:00:00.000Z"
    holiday = window_to_utc({"start": "2025-11-01T00:00:00", "end": "2026-01-01T00:00:00"}, zone)
    assert holiday == {"start": f"2025-11-01T{summer}:00:00.000Z", "end": f"2026-01-01T{winter}:00:00.000Z"}


@pytest.mark.parametrize("zone", ["America/New_York", "America/Chicago"])
def test_daily_windows_across_dst_have_23_or_25_hours(zone):
    for start, end, hours in (("2026-03-08", "2026-03-09", 23), ("2026-11-01", "2026-11-02", 25)):
        w = window_to_utc({"start": start, "end": end}, zone)
        span = datetime.fromisoformat(w["end"].replace("Z", "+00:00")) - datetime.fromisoformat(w["start"].replace("Z", "+00:00"))
        assert span.total_seconds() / 3600 == hours


@pytest.mark.parametrize("zone", ["America/New_York", "America/Chicago"])
def test_explicit_instants_keep_their_instant(zone):
    now = "2026-10-02T22:04:45.178Z"
    assert boundary_to_utc(now, zone) == now
    assert boundary_to_utc(datetime(2026, 10, 2, 22, 4, 45, 178000, tzinfo=timezone.utc), zone) == now
    assert boundary_to_utc("2026-10-01T00:00:00+00:00", zone) == "2026-10-01T00:00:00.000Z"
    assert window_to_utc({"start": "2025-11-01T00:00:00", "end": now}, zone)["end"] == now


def test_invalid_and_ambiguous_boundaries_fail():
    with pytest.raises(ValueError):
        boundary_to_utc("2026-02-30T00:00:00", "America/New_York")
    with pytest.raises(ValueError, match="keep Z"):
        boundary_to_utc("2026-10-02T22:04:45", "America/Chicago")
    with pytest.raises(ValueError, match="missing or ambiguous"):
        boundary_to_utc("2011-12-30", "Pacific/Apia")  # Samoa skipped this day
    with pytest.raises(ZoneInfoNotFoundError):
        boundary_to_utc("2026-01-01", "Invalid/Timezone")
    with pytest.raises(ValueError, match="end must follow"):
        window_to_utc({"start": "2026-01-02", "end": "2026-01-01"}, "UTC")


def test_month_start_uses_the_account_calendar():
    now = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)  # still Sep 30 in Los Angeles
    assert month_start(now, "America/Los_Angeles", 0) == "2026-09-01"
    assert month_start(now, "UTC", 0) == "2026-10-01"
    assert month_start(now, "UTC", -11) == "2025-11-01"
    assert month_start(now, "UTC", -23) == "2024-11-01"


# ---------------------------------------------------------------- order items

def test_empty_top_level_arrays_dont_mask_line_items():
    props = {"ItemNames": [], "Items": [], "Name": "CUSTOMER NAME MUST NOT BE COPIED",
             "$extra": {"email": "customer@example.invalid",
                        "shipping_address": {"name": "CUSTOMER NAME MUST NOT BE COPIED"},
                        "line_items": [{"title": "D-Bloat", "sku": "DB01", "properties": {"email": "customer@example.invalid"}},
                                       {"name": "B-Clear", "quantity": 2, "customer": {"name": "CUSTOMER NAME MUST NOT BE COPIED"}}]}}
    assert item_names(props) == ["D-Bloat", "B-Clear"]


def test_numeric_items_objects_keep_order_and_only_titles():
    props = {"Items": {"10": {"ProductName": "Third", "email": "customer@example.invalid"},
                       "2": {"Name": "Second", "address": {"name": "PRIVATE"}},
                       "0": {"title": "First", "properties": [{"name": "PRIVATE"}]}}}
    assert item_names(props) == ["First", "Second", "Third"]


def test_arbitrary_objects_never_become_titles():
    assert item_names({"ItemNames": [{"customer": {"name": "PRIVATE"}}], "Items": {"customer": {"Name": "PRIVATE"}},
                       "$extra": {"line_items": [{"title": {"name": "PRIVATE"}}, {"name": ["PRIVATE"]},
                                                 {"email": "customer@example.invalid"}, {"title": "  ", "name": None},
                                                 {"title": "Valid product", "customer": {"name": "PRIVATE"}}]}}) == ["Valid product"]
    assert item_names({"Name": "PRIVATE", "email": "customer@example.invalid"}) == []


def test_existing_item_shapes():
    assert item_names({"ItemNames": [" D-Bloat "], "Items": [{"Name": "D-Bloat"}]}) == ["D-Bloat"]
    assert item_names({"Items": [{"ProductName": "One"}, {"Name": "Two"}, {"name": "Three"}]}) == ["One", "Two", "Three"]
    assert item_names({"Items": {}, "$extra": {"line_items": [{"title": "Fallback"}]}}) == ["Fallback"]
    assert item_names(None) == []


# ---------------------------------------------------------------- metric selection

def test_shopify_wins_over_a_dormant_commerce_metric():
    ms = [metric("WOO", "Placed Order", "WooCommerce"), metric("SHOP", "Placed Order", "Shopify")]
    choice = pick_metric(ms, "Placed Order", "ecommerce")
    assert choice["status"] == "selected" and choice["metric"]["id"] == "SHOP"
    d = describe(choice)
    assert d["used"] == {"id": "SHOP", "name": "Placed Order", "integration": "Shopify"}
    assert [c["id"] for c in d["otherCandidates"]] == ["WOO"]


def test_two_non_shopify_commerce_metrics_are_ambiguous_never_guessed():
    ms = [metric("WOO", "Placed Order", "WooCommerce"), metric("BIG", "Placed Order", "BigCommerce")]
    choice = pick_metric(ms, "Placed Order", "ecommerce")
    assert choice["status"] == "ambiguous" and choice["metric"] is None
    assert "WOO" in choice["reason"] and "BIG" in choice["reason"]
    assert describe(choice)["used"] is None


def test_sms_aliases_resolve_and_prefer_klaviyo():
    ms = [metric("R1", "Received Text Message", "Klaviyo"), metric("A1", "Received SMS", "Attentive")]
    choice = pick_metric(ms, "Received SMS", "klaviyo")
    assert choice["metric"]["id"] == "R1" and len(choice["candidates"]) == 2


def test_name_priority_only_inside_one_integration():
    same = [metric("F1", "Submitted Form", "Klaviyo"), metric("F2", "Form submitted by profile", "Klaviyo")]
    assert pick_metric(same, ["Form submitted by profile", "Submitted Form"], "klaviyo")["metric"]["id"] == "F2"
    mixed = [metric("U1", "Unsubscribed", "Klaviyo"), metric("U2", "Unsubscribed from Email Marketing", "Other")]
    assert pick_metric(mixed, ["Unsubscribed from Email Marketing", "Unsubscribed"])["status"] == "ambiguous"


def test_missing_metric():
    assert pick_metric([metric("X", "Viewed Product", "Shopify")], "Placed Order", "ecommerce")["status"] == "missing"


def test_pins_win_and_fail_closed():
    ms = [metric("WOO", "Placed Order", "WooCommerce"), metric("SHOP", "Placed Order", "Shopify")]
    pins = {"Placed Order": {"id": "WOO", "integration": "WooCommerce"}}
    choice = pick_metric(ms, "Placed Order", "ecommerce", pins)
    assert choice["metric"]["id"] == "WOO" and "Pinned" in choice["reason"]
    with pytest.raises(PinError, match="no longer"):
        pick_metric(ms, "Placed Order", "ecommerce", {"Placed Order": {"id": "GONE", "integration": None}})
    with pytest.raises(PinError, match="comes from"):
        pick_metric(ms, "Placed Order", "ecommerce", {"Placed Order": {"id": "SHOP", "integration": "WooCommerce"}})
    with pytest.raises(PinError, match="now called"):
        pick_metric([metric("WOO", "Ordered Product", "WooCommerce")], "Placed Order", "ecommerce",
                    {"Placed Order": {"id": "WOO", "integration": None}})
    # A pin for another event doesn't affect this one.
    assert pick_metric(ms, "Placed Order", "ecommerce", {"Viewed Product": {"id": "V", "integration": None}})["metric"]["id"] == "SHOP"
