"""Phase 2: the analysis modules on one synthetic account built to trip every finding (synthetic data only)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from wavyos import analysis
from wavyos.analysis import messages as msgmod
from wavyos.audit import opportunities
from wavyos.audit.library import specs as library_specs
from wavyos.audit.snapshot import Snapshot

END = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
CUR = [f"{y}-{mo:02d}-01T00:00:00+00:00" for y, mo in [(2025, 10), (2025, 11), (2025, 12)] + [(2026, m) for m in range(1, 11)]]
PRIOR = [d.replace("2026-", "2025-X").replace("2025-1", "2024-1").replace("2025-X", "2025-") for d in CUR]
WINDOWS = {"current": {"start": "2025-10-01T00:00:00+00:00", "end": END.isoformat()},
           "prior": {"start": "2024-10-01T00:00:00+00:00", "end": "2025-10-11T12:00:00+00:00"}}


def _series(cur, prior=None, dims=None, measurement="sum_value"):
    def window(dates, v):
        rows = [{"dimensions": [d] if d else [], "measurements": {measurement: [v] * len(dates), "count": [v] * len(dates),
                                                                    "unique": [v] * len(dates)}} for d in (dims or [None])]
        return {"dates": dates, "rows": rows}
    out = {"current": window(CUR, cur)}
    if prior is not None:
        out["prior"] = window(PRIOR, prior)
    return out


def _report(rows):
    return {"ok": True, "value": {"data": {"attributes": {"results": rows}}}}


def _campaigns():
    items, results = [], []
    week0 = datetime(2025, 10, 6, tzinfo=timezone.utc)
    for k in range(52):
        heavy = k % 4 == 0
        for i in range(4 if heavy else 1):
            cid = f"C{k:02d}{i}"
            name, rpr = (("Mega Sale 40% off", 0.05) if heavy else
                         ("Black Friday 30% off", 0.10) if k == 7 else ("Flash Sale 15% off", 0.20))
            sent = week0 + timedelta(weeks=k, days=2, hours=10 + i)
            items.append({"id": cid, "attributes": {"name": name, "status": "Sent", "send_time": sent.isoformat()}})
            results.append({"groupings": {"campaign_id": cid, "campaign_message_name": name, "send_channel": "email"},
                            "statistics": {"recipients": 2000, "delivered": 2000, "conversion_value": rpr * 2000,
                                           "unsubscribes": 2, "spam_complaints": 0}})
    return items, results


def _flow_rows(rows):
    out = []
    for fid, name, rec, rpr, unsub, spam in rows:
        out.append({"groupings": {"flow_id": fid, "flow_name": name, "flow_message_id": f"{fid}-M1",
                                  "flow_message_name": f"{name} 1", "send_channel": "email"},
                    "statistics": {"recipients": rec, "delivered": rec, "conversion_value": rec * rpr,
                                   "unsubscribes": unsub, "spam_complaints": spam, "bounced": 0}})
    return out


def _orders():
    rows, n = [], 0
    for b in range(200):  # first-observed buyers between 600 and 200 days before the end
        first = END - timedelta(days=600 - 2 * b)
        disc = b % 2 == 0
        src = "tiktok_shop" if b % 10 < 3 else "web"
        rows.append({"p": f"P{b:03d}", "t": first.isoformat(), "v": 40.0, "o": f"o{n}", "d": "CODE10" if disc else None,
                     "s": src})
        n += 1
        if not disc and b % 5 == 1:  # 20% of full-price buyers come back within 60 days
            rows.append({"p": f"P{b:03d}", "t": (first + timedelta(days=60)).isoformat(), "v": 40.0, "o": f"o{n}",
                         "s": src})
            n += 1
    return {"start": (END - timedelta(days=730)).isoformat(), "end": END.isoformat(), "orders": rows}


def account(**override) -> dict:
    items, camp_results = _campaigns()
    totals = {
        "capturedAt": END.isoformat(), "account": {"timezone": "UTC"}, "windows": WINDOWS,
        "metrics": [{"id": "M1", "name": "Subscription Started"}, {"id": "M2", "name": "Subscription Cancelled"},
                    {"id": "M3", "name": "Subscription Payment Failed"}],
        "aggregates": {
            "placed_order": _series(10000, 15000),
            "placed_order_by_channel": _series(1000, 1000, dims=["$email_channel"]),
            "refunded_order": _series(800, 500),
            "subscribed_email": _series(300, 300, measurement="unique"),
            "unsubscribed_email": _series(100, 100, measurement="unique"),
            "subscribed_sms": _series(50, measurement="unique"),
            "subscription_M1": _series(10, measurement="count"),
            "subscription_M2": _series(20, measurement="count"),
            "subscription_M3": _series(5, measurement="count"),
        },
        "campaigns": {"email": {"items": items, "messages": []}, "sms": {"items": []}},
        "reports": {"formValues365": {"data": {"attributes": {"results": [
            {"groupings": {"form_id": "FA", "form_version_id": "v1"}, "statistics": {"viewed_form_uniques": 5000, "submits": 50}},
            {"groupings": {"form_id": "FB", "form_version_id": "v1"}, "statistics": {"viewed_form_uniques": 800, "submits": 80}}]}}}},
        "groupCounts": {"segments": [{"id": "S1", "name": "Engaged 90 days", "profileCount": 4000}]},
    }
    flows = [("F1", "Welcome", 5000, 2.0, 10, 0), ("F2", "Abandoned Checkout", 3000, 3.0, 5, 0),
             ("F3", "Retention.com Flow", 20000, 0.05, 400, 80), ("F4", "Browse Abandonment", 2000, 1.0, 4, 0)]
    perf = {"capturedAt": END.isoformat(), "reports": {
        "flow365": _report(_flow_rows(flows)),
        "flow90": _report(_flow_rows([("F1", "Welcome", 1000, 0.5, 2, 0), ("F2", "Abandoned Checkout", 800, 3.0, 1, 0),
                                      ("F3", "Retention.com Flow", 5000, 0.05, 100, 20)])),
        "campaign365": _report(camp_results)}}
    templates = {"capturedAt": END.isoformat(), "templates": [{
        "templateId": "T1", "templateName": "Welcome 1", "htmlBytes": 40000, "imageCount": 3, "textChars": 900,
        "imagesWithoutAlt": 2, "hasUnsubscribe": False, "hasAddress": True,
        "text": "Our formula cures dull skin. Clinically proven. Treat yourself to high quality.",
        "messages": [{"flowName": "Welcome", "messageName": "Email 1", "subject": "Welcome in"}],
        "links": ["https://shop.example.com/a", "https://shop.example.com/b", "http://old-shop.example.net/x"]}]}
    api = {"capturedAt": END.isoformat(), "flows": {"items": [], "details": []},
           "forms": {"items": [{"id": "FA", "attributes": {"name": "Homepage popup", "status": "live"}},
                               {"id": "FB", "attributes": {"name": "Footer", "status": "live"}}]},
           "metrics": {"items": []}}
    files = {"totals": totals, "perf": perf, "templates": templates, "api": api, "orders": _orders()}
    files.update(override)
    return files


def fake_fetch(url):
    if url.endswith("/b"):
        return {"status": 404, "final_url": url, "error": None}
    return {"status": 200, "final_url": url, "error": None}


@pytest.fixture(scope="module")
def result():
    return analysis.run_all(Snapshot.from_dicts(account()), {"regulated_category": "No"}, fetch=fake_fetch)


EXPECTED = {
    "kpi_scorecard.revenue_down", "kpi_scorecard.owned_share_low", "performance.refunds_high", "performance.off_site_orders",
    "cohorts.discount_buyers_repeat_less", "cohorts.low_early_repeat", "flow_history.declining_flows",
    "flow_history.weak_flows", "flow_history.messages_driving_people_away", "complaints.complaint_concentration",
    "complaints.unsubscribe_concentration", "program.thin_peak_season", "program.send_fatigue",
    "promotions.best_promo_beat_bf", "promotions.deep_discounts_no_better", "capture_reach.weak_forms",
    "subscriptions.subscribers_shrinking", "subscriptions.failed_unmessaged", "messages.claims_risk",
    "messages.missing_alt_text", "messages.no_unsubscribe", "messages.link_problems",
}


def test_every_module_runs_or_says_why_not(result):
    status = {k: v["status"] for k, v in result["modules"].items()}
    assert not [k for k, s in status.items() if s == "error"], result["unavailable"]
    assert status["journey_graph"] == "unavailable" and "flow definitions" in result["unavailable"]["journey_graph"]
    assert set(status) == {mod.MODULE for mod in analysis.MODULES}


def test_the_synthetic_account_trips_every_finding(result):
    found = {f["id"] for f in result["findings"]}
    assert EXPECTED <= found, EXPECTED - found


def test_every_finding_is_actionable_and_quotes_only_recorded_facts(result):
    ledger = {f["id"] for f in result["facts"]}
    for f in result["findings"]:
        assert f["finding"].strip() and f["evidence"].strip() and f["action"].strip(), f["id"]
        assert set(f["facts"]) <= ledger, (f["id"], set(f["facts"]) - ledger)
        assert f["severity"] in ("critical", "high", "medium", "low")


def test_unmeasured_is_none_not_zero():
    files = account()
    del files["totals"]["aggregates"]["refunded_order"]
    out = analysis.run_all(Snapshot.from_dicts(files), {})
    refund = next(f for f in out["facts"] if f["id"] == "performance.refund_rate_12m")
    assert refund["value"] is None and "no Refunded Order" in refund["label"]


def test_findings_join_the_ranked_cards_and_unavailable_modules_are_gaps(result):
    snap = Snapshot.from_dicts(account())
    checks = {"FLW-01": {"verdict": "pass", "message": "ok", "evidence": []}}
    out = opportunities.build(snap, checks, library_specs(), findings=result["findings"], unavailable=result["unavailable"])
    cards = out["opportunities"]
    assert len(cards) == len(result["findings"])
    assert all(c["status"] == "Proposed" and c["owner"] == "UNCONFIRMED" and c["steps"] and c["observation"] for c in cards)
    assert cards[0]["category"] == "fix_first"
    assert [c["rank"] for c in cards] == list(range(1, len(cards) + 1))
    assert any(g.get("module") == "journey_graph" and g["reason"] for g in out["gaps"])
    sized = [c for c in cards if c["sizing"] and c["sizing"].get("status") == "sized"]
    assert sized, "post-purchase findings carry the measured post_purchase lever"


def test_a_crashing_module_is_reported_and_the_rest_still_run(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bad input")
    monkeypatch.setattr(analysis.promotions, "run", boom)
    out = analysis.run_all(Snapshot.from_dicts(account()), {})
    assert out["modules"]["promotions"]["status"] == "error" and "bad input" in out["unavailable"]["promotions"]
    assert out["modules"]["program"]["status"] == "ok"


def test_claims_terms_depend_on_the_category():
    plain = msgmod._terms({"regulated_category": "No"})
    cbd = msgmod._terms({"regulated_category": "CBD / hemp / THC", "avoid_claims": "anti-aging, detox"})
    text = "treat yourself to high quality, it treats anxiety and helps detox"
    assert msgmod._hits(text, plain) == []
    assert set(msgmod._hits(text, cbd)) == {"treats", "anxiety", "detox"}


def test_link_checker_flags_offline_and_fetched_problems(result):
    issues = {(i["url"], i["issue"]) for i in result["modules"]["messages"]["tables"]["link_issues"]}
    assert ("http://old-shop.example.net/x", "not_https") in issues
    assert ("http://old-shop.example.net/x", "off_domain") in issues
    assert ("https://shop.example.com/b", "broken") in issues


def test_the_fetcher_refuses_private_addresses():
    from wavyos.analysis.links import safe_fetch
    assert "refused" in safe_fetch("http://127.0.0.1/admin")["error"]
    assert "refused" in safe_fetch("http://localhost:8080/")["error"]


def test_send_history_has_52_weeks_and_a_calendar(result):
    weekly = result["modules"]["program"]["tables"]["weekly"]
    assert len(weekly) == 52 and sum(w["email"] for w in weekly) == 13 * 4 + 39
    assert len(result["modules"]["program"]["tables"]["calendar"]) == 91


def test_cohort_horizons_are_mature_only(result):
    cohorts = result["modules"]["cohorts"]["tables"]["monthly_cohorts"]
    youngest = cohorts[-1]
    assert youngest["d365"] is None  # buyers from ~200 days ago can't have a 365-day number yet
