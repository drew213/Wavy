"""Onboarding from a single Klaviyo key, end to end against a recorded fake Klaviyo account."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import wavyos.jobmods  # noqa: F401
from wavyos import demo, jobs, storage
from wavyos import models as m
from wavyos.api import create_app
from wavyos.connectors.klaviyo import KlaviyoClient, KlaviyoError
from wavyos.db import create_all, make_engine, make_sessionmaker
from wavyos.vault import LocalKeyProvider, Vault

KEY = "pk_live_test_secret_value_123"
NOW = datetime.now(timezone.utc)


def _event(eid, profile, days_ago, value, item):
    return {"type": "event", "id": f"evt-{eid}",
            "attributes": {"datetime": (NOW - timedelta(days=days_ago)).isoformat(),
                           "event_properties": {"$event_id": f"order-{eid}", "$value": value, "ItemNames": [item]}},
            "relationships": {"profile": {"data": {"type": "profile", "id": profile}}}}


PAGE_1 = [_event(1, "P1", 90, 60.0, "Barrier Serum"), _event(2, "P2", 80, 100.0, "Cleanser")]
PAGE_2 = [_event(3, "P1", 60, 80.0, "Barrier Serum"), _event(4, "P3", 10, 40.0, "Cleanser")]


def fake_klaviyo(account_id="ACC1", calls=None, no_orders=False):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Klaviyo-API-Key {KEY}"
        path = request.url.path.rstrip("/")
        if calls is not None:
            calls.append((request.method, path))
        if path == "/api/accounts":
            return httpx.Response(200, json={"data": [{"id": account_id, "attributes": {
                "contact_information": {"organization_name": "Glow Lab"}, "timezone": "America/New_York",
                "preferred_currency": "USD"}}]})
        if path == "/api/metrics":
            return httpx.Response(200, json={"data": [
                {"id": "WOO", "attributes": {"name": "Placed Order", "integration": {"key": "woocommerce"}}},
                {"id": "PO1", "attributes": {"name": "Placed Order", "integration": {"key": "shopify"}}}]})
        if path == "/api/metric-aggregates":
            body = json.loads(request.content)["data"]["attributes"]
            assert body["metric_id"] in {"PO1", "WOO"}
            if body["metric_id"] == "WOO" or no_orders:  # a dormant copy of Placed Order: no orders in the window
                return httpx.Response(200, json={"data": {"attributes": {"dates": [], "data": [
                    {"measurements": {"count": [0], "sum_value": [0.0]}}]}}})
            dates = [f"2026-{mo:02d}-01T00:00:00+00:00" for mo in range(1, 13)]
            return httpx.Response(200, json={"data": {"attributes": {"dates": dates, "data": [{"measurements": {
                "count": [0] * 9 + [1, 2, 1], "sum_value": [0] * 9 + [60.0, 180.0, 40.0]}}]}}})
        if path == "/api/events":
            if no_orders:
                return httpx.Response(200, json={"data": [], "links": {"next": None}})
            if request.url.params.get("page[cursor]"):
                return httpx.Response(200, json={"data": PAGE_2, "links": {"next": None}})
            since = request.url.params["filter"].split("greater-or-equal(datetime,")[1].split(")")[0]
            newer = [e for e in PAGE_1 + PAGE_2 if e["attributes"]["datetime"] >= since.replace("Z", "+00:00")]
            if len(newer) < len(PAGE_1 + PAGE_2):
                return httpx.Response(200, json={"data": newer, "links": {"next": None}})
            return httpx.Response(200, json={"data": PAGE_1, "links": {
                "next": "https://a.klaviyo.com/api/events/?page%5Bcursor%5D=abc"}})
        if path == "/api/flows":
            return httpx.Response(200, json={"data": [
                {"id": "F1", "attributes": {"name": "Abandoned Checkout - Core", "status": "live"}},
                {"id": "F2", "attributes": {"name": "Browse Abandonment", "status": "draft"}},
                {"id": "F3", "attributes": {"name": "Welcome Series", "status": "live"}},
                {"id": "F4", "attributes": {"name": "Winback 90d", "status": "live"}}]})
        if path == "/api/flow-values-reports":
            return httpx.Response(200, json={"data": {"attributes": {"results": [
                {"groupings": {"flow_id": "F1", "flow_name": "Abandoned Checkout - Core"}, "statistics": {"conversion_value": 30.0}},
                {"groupings": {"flow_id": "F4", "flow_name": "Old Winback (archived)"}, "statistics": {"conversion_value": 20.0}}]}}})
        if path == "/api/campaign-values-reports":
            return httpx.Response(200, json={"data": {"attributes": {"results": [
                {"groupings": {"campaign_id": "C1"}, "statistics": {"conversion_value": 10.0}}]}}})
        return httpx.Response(404, json={"errors": [{"detail": f"unexpected {path}"}]})
    return handler


@pytest.fixture()
def env(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'k.db'}")
    create_all(eng)
    factory = make_sessionmaker(eng)
    token = demo.build(factory)
    vault = Vault(LocalKeyProvider(tmp_path / "master.key"))

    def verify(key):
        if key != KEY:
            raise KlaviyoError("Klaviyo GET /api/accounts/ failed: HTTP 401", status=401)
        return {"id": "ACC1", "name": "Glow Lab", "timezone": "America/New_York"}

    app = create_app(factory=factory, services={"vault": vault, "klaviyo_verify": verify})
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer " + token
    return factory, client, vault, tmp_path / "k.db"


def _services(vault, transport):
    return {"vault": vault, "klaviyo_factory": lambda key: KlaviyoClient(
        key, transport=transport, pace=False, sleep=lambda s: None)}


def _onboard(c):
    r = c.post("/v1/ops/onboard", json={"client_name": "Glow Lab", "client_slug": "glow-k", "klaviyo_private_key": KEY})
    assert r.status_code == 200, r.text
    return r.json()


def test_klaviyo_check_names_the_account_and_rejects_bad_keys(env):
    _, c, _, _ = env
    ok = c.post("/v1/ops/onboard/klaviyo-check", json={"api_key": KEY})
    assert ok.status_code == 200 and ok.json()["name"] == "Glow Lab"
    bad = c.post("/v1/ops/onboard/klaviyo-check", json={"api_key": "pk_wrong"})
    assert bad.status_code == 422 and "read-only" in bad.text
    assert "pk_wrong" not in bad.text
    refused = c.post("/v1/ops/onboard", json={"client_name": "X", "client_slug": "x", "klaviyo_private_key": "pk_wrong"})
    assert refused.status_code == 422


def test_klaviyo_key_alone_produces_a_measured_audit(env):
    factory, c, vault, db_file = env
    queued = _onboard(c)
    calls = []
    done = jobs.run_pending(factory, _services(vault, httpx.MockTransport(fake_klaviyo(calls=calls))), limit=10)
    run = next(r for r in done if r.id == queued["job_run_id"])
    assert run.status == "succeeded", run.error
    assert all(method == "GET" or "report" in path or "aggregate" in path for method, path in calls)

    body = c.get(f"/v1/ops/onboard/{queued['job_run_id']}").json()
    stages = {s["key"]: s["status"] for s in body["progress"]["stages"]}
    assert stages.pop("sizing") == "skipped" and set(stages.values()) == {"done"}, stages
    assert body["progress"]["metrics"]["orders_imported"] == 4
    assert body["progress"]["metrics"]["orders_expected"] == 4
    assert len(body["progress"]["metrics"]["months"]) == 12

    d = body["result"]["dossier"]
    assert d["data_sources"] == {"orders": "klaviyo", "klaviyo": True}
    assert d["economics"]["target_aov"] == 70.0
    assert d["economics"]["median_repurchase_days"] == 30
    assert d["economics"]["repeat_purchase_rate"] == pytest.approx(0.333, abs=0.001)
    cov = d["klaviyo"]["coverage"]
    assert cov["abandoned_checkout"]["live"] and cov["customer_winback"]["live"]
    assert not cov["browse_abandonment"]["live"] and not cov["added_to_cart"]["live"]
    assert d["klaviyo"]["revenue_12m"] == {"flows": 50.0, "by_flow": {"F1": 30.0, "F4": 20.0}, "campaigns": 10.0,
                                           "email_total": 60.0, "email_share": pytest.approx(60 / 280, abs=1e-4),
                                           "flow_names": {"F1": "Abandoned Checkout - Core", "F4": "Old Winback (archived)"}}
    flows = {f["flow_key"]: f for f in d["execution"]["flows"]}
    assert flows["abandoned_checkout"]["already_live"] is True
    assert flows["browse_abandonment"]["already_live"] is False
    assert d["klaviyo"]["metric_choice"]["metric_id"] == "PO1"
    assert {c["id"]: c["orders_last_90_days"] for c in d["klaviyo"]["metric_choice"]["candidates"]}["WOO"] == 0
    assert d["sizing_summary"] is None and body["result"]["sizing_report"] == {}
    assert body["result"]["overall_health_score"] == d["health"]["score"]
    assert {p["key"] for p in d["health"]["parts"]} == {"flow_coverage", "email_share", "repeat_rate"}

    with factory() as s:
        assert s.scalar(select(func.count()).select_from(m.CommerceOrder)) == 4
        conn = s.scalar(select(m.Connection).where(m.Connection.provider == "klaviyo"))
        assert conn.external_account_id == "ACC1"
    assert KEY not in json.dumps(run.trigger) and KEY not in json.dumps(body)
    assert not db_file.exists() or KEY.encode() not in db_file.read_bytes()  # no file on Postgres runs


def test_rerun_resumes_from_the_watermark_without_duplicates(env):
    factory, c, vault, _ = env
    services = _services(vault, httpx.MockTransport(fake_klaviyo()))
    _onboard(c)
    jobs.run_pending(factory, services, limit=10)
    second = _onboard(c)
    jobs.run_pending(factory, services, limit=10)
    body = c.get(f"/v1/ops/onboard/{second['job_run_id']}").json()
    assert body["status"] == "succeeded" and body["dossier_version"] == 2
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(m.CommerceOrder)) == 4


def test_a_large_backfill_continues_in_a_follow_up_run(env, monkeypatch):
    factory, c, vault, _ = env
    monkeypatch.setattr(storage, "KLAVIYO_PAGES_PER_RUN", 1)
    queued = _onboard(c)
    done = jobs.run_pending(factory, _services(vault, httpx.MockTransport(fake_klaviyo())), limit=10)
    onboards = [r for r in done if r.job_key == "client.onboard"]
    assert [r.status for r in onboards] == ["succeeded", "succeeded"], [r.error for r in onboards]
    first = c.get(f"/v1/ops/onboard/{queued['job_run_id']}").json()
    assert first["result"]["dossier"]["klaviyo"]["orders"]["backfill_complete"] is False
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(m.CommerceOrder)) == 4
        latest = storage.latest_dossier(s, onboards[1].client_id)
        assert latest.document["dossier"]["klaviyo"]["orders"]["backfill_complete"] is True


def test_a_key_that_opens_another_account_stops_the_run(env):
    factory, c, vault, _ = env
    queued = _onboard(c)
    done = jobs.run_pending(factory, _services(vault, httpx.MockTransport(fake_klaviyo(account_id="OTHER"))), limit=10)
    run = next(r for r in done if r.id == queued["job_run_id"])
    assert run.status == "failed" and "different account" in run.error


def test_a_store_with_no_readable_orders_is_unavailable_not_benchmarked(env):
    factory, c, vault, _ = env
    queued = _onboard(c)
    jobs.run_pending(factory, _services(vault, httpx.MockTransport(fake_klaviyo(no_orders=True))), limit=10)
    body = c.get(f"/v1/ops/onboard/{queued['job_run_id']}").json()
    assert body["status"] == "succeeded", body.get("error")
    d = body["result"]["dossier"]
    assert d["data_sources"]["orders"] == "unavailable"
    assert d["economics"]["target_aov"] is None and d["economics"]["repeat_purchase_rate"] is None
    assert d["sizing_summary"] is None and d["health"]["score"] is None
    assert "80.0" not in json.dumps(d["economics"])
