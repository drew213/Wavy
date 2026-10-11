"""The audit collector end to end against a synthetic Klaviyo account (no client data).

The fake serves every endpoint the stages call, in Klaviyo's JSON:API shapes. Event properties carry an
email address and a customer name on purpose: neither may reach an artifact or a stored order.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import func, select

import wavyos.jobmods  # noqa: F401  (registers audit.collect)
from wavyos import connections, jobs
from wavyos import models as m
from wavyos.audit.engine import evaluate
from wavyos.collector import budget
from wavyos.collector import run as collector
from wavyos.collector import stages
from wavyos.connectors.klaviyo import KlaviyoClient

KEY = "pk_test_collector_0123456789abcdef"
NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}")
PRIVATE = "CUSTOMER NAME MUST NOT BE COPIED"

METRICS = [("PO1", "Placed Order", "Shopify"), ("WOO", "Placed Order", "WooCommerce"),
           ("SC1", "Started Checkout", "Shopify"), ("ATC1", "Added to Cart", "Shopify"),
           ("VP1", "Viewed Product", "Shopify"), ("RE1", "Received Email", "Klaviyo"),
           ("OE1", "Opened Email", "Klaviyo"), ("CE1", "Clicked Email", "Klaviyo"),
           ("BE1", "Bounced Email", "Klaviyo"), ("UE1", "Unsubscribed from Email Marketing", "Klaviyo"),
           ("SE1", "Subscribed to Email Marketing", "Klaviyo"), ("SUB1", "Subscription Started", "Recharge")]


class Crash(BaseException):
    """Stands in for the worker process dying mid-stage."""


def _email(aid, template, nxt=None):
    return {"id": aid, "type": "send-email", "links": {"next": nxt},
            "data": {"status": "live", "message": {"id": f"{aid}-msg", "name": aid, "subject": f"Subject {aid}",
                                                   "template_id": template, "smart_sending_enabled": True,
                                                   "add_tracking_params": True}}}


FLOWS = {
    "F_WEL": ("Welcome", "live", "Added to List", {"triggers": [{"type": "list", "id": "L1"}],
                                                   "actions": [_email("w1", "T1", "wd"), {"id": "wd", "type": "time-delay",
                                                               "links": {"next": "w2"}, "data": {"unit": "days", "value": 1}},
                                                               _email("w2", "T2")]}),
    "F_CHK": ("Checkout", "live", "Metric", {"triggers": [{"type": "metric", "id": "SC1"}], "actions": [_email("c1", "T3")]}),
    "F_WB": ("Winback", "draft", "Metric", {"triggers": [{"type": "metric", "id": "PO1"}], "actions": [_email("wb1", "T4")]}),
}


def _order(n: int) -> dict:
    t = NOW - timedelta(days=700 - n * 23)
    return {"type": "event", "id": f"evt{n:03d}",
            "attributes": {"datetime": t.isoformat(), "event_properties": {
                "$event_id": f"order-{n}", "$value": 40.0 + n, "Source Name": "web",
                "ItemNames": [], "Discount Codes": ["WELCOME10"] if n % 3 == 0 else [],
                "$extra": {"email": f"buyer{n}@example.com", "line_items": [{"title": "Serum", "customer": {"name": PRIVATE}}]}}},
            "relationships": {"profile": {"data": {"type": "profile", "id": f"P{n % 12:02d}"}}}}


ORDERS = [_order(n) for n in range(30)]


class FakeKlaviyo:
    def __init__(self, *, account="ACC1", page=5, crash_on_event_page=None, reports_fail=False, dormant="WOO"):
        self.account, self.page, self.crash_on = account, page, crash_on_event_page
        self.reports_fail, self.dormant = reports_fail, dormant
        self.calls: list[tuple[str, str, dict]] = []
        self.event_pages = 0
        self.since = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> KlaviyoClient:
        return KlaviyoClient(KEY, transport=self.transport(), pace=False, sleep=lambda s: None)

    def _ok(self, data, included=None, nxt=None):
        body = {"data": data, "links": {"next": nxt}}
        if included is not None:
            body["included"] = included
        return httpx.Response(200, json=body)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        params = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path, params))
        if path == "/api/accounts":
            return self._ok([{"type": "account", "id": self.account, "attributes": {
                "contact_information": {"organization_name": "Synthetic Shop"}, "timezone": "America/New_York",
                "preferred_currency": "USD"}}])
        if path == "/api/metrics":
            return self._ok([{"type": "metric", "id": i, "attributes": {"name": n, "integration": {"name": g, "key": g.lower()}}}
                             for i, n, g in METRICS])
        if path == "/api/flows":
            return self._ok([{"type": "flow", "id": fid, "attributes": {"name": n, "status": s, "archived": False, "trigger_type": t}}
                             for fid, (n, s, t, _) in FLOWS.items()])
        if path.startswith("/api/flows/"):
            fid = path.split("/")[3]
            n, s, t, defn = FLOWS[fid]
            assert params == {"additional-fields[flow]": "definition"}
            return self._ok({"type": "flow", "id": fid, "attributes": {"name": n, "status": s, "definition": defn}})
        if path.startswith("/api/templates/"):
            tid = path.split("/")[3]
            html = "<html><img src=x><a href=y>Shop</a>{{ first_name }}" + ("x" * (110_000 if tid == "T2" else 10)) + "</html>"
            return self._ok({"type": "template", "id": tid, "attributes": {"name": f"Template {tid}", "html": html}})
        if path == "/api/templates":
            return self._ok([{"type": "template", "id": "T1", "attributes": {"name": "Template T1"}}])
        if path == "/api/forms":
            return self._ok([{"type": "form", "id": "FORM1", "attributes": {"name": "Popup", "status": "live"}}])
        if path == "/api/lists":
            return self._ok([{"type": "list", "id": "L1", "attributes": {"name": "Newsletter"}}])
        if path == "/api/segments":
            return self._ok([{"type": "segment", "id": f"SEG{i}", "attributes": {"name": n, "is_active": True, "definition": {}}}
                             for i, n in enumerate(["Engaged 90 days", "Unengaged 180 days", "VIP"])])
        if path.startswith("/api/segments/"):
            assert params == {"additional-fields[segment]": "profile_count"}
            return self._ok({"type": "segment", "id": path.split("/")[3], "attributes": {"profile_count": 1234}})
        if path == "/api/campaigns":
            if "'sms'" in params["filter"]:
                return self._ok([], included=[])
            camps = [{"type": "campaign", "id": f"C{i}", "attributes": {"name": f"Campaign {i}", "status": "Sent",
                      "send_time": (NOW - timedelta(days=3 * i)).isoformat(), "archived": False,
                      "audiences": {"included": ["SEG0"], "excluded": []}, "send_options": {"use_smart_sending": True}}}
                     for i in range(4)]
            msgs = [{"type": "campaign-message", "id": f"CM{i}", "attributes": {"label": f"Campaign {i}"}} for i in range(4)]
            return self._ok(camps, included=msgs)
        if path == "/api/metric-aggregates":
            attrs = body["data"]["attributes"]
            assert attrs["timezone"] == "America/New_York" and attrs["interval"] == "month"
            start = datetime.fromisoformat(attrs["filter"][0].split(",", 1)[1].rstrip(")").replace("Z", "+00:00"))
            dates = [(start + timedelta(days=31 * k)).replace(day=1).isoformat() for k in range(12)]
            dims = {"$attributed_channel": ["$email_channel", "$sms_channel", ""], "Inbox Provider": ["Gmail", "Yahoo"],
                    "Bounce Type": ["hard", "soft"]}.get((attrs.get("by") or [None])[0], [None])
            level = 0.0 if attrs["metric_id"] == self.dormant else 10.0
            rows = [{"dimensions": [d] if d is not None else [], "measurements": {k: [level] * 12 for k in attrs["measurements"]}}
                    for d in dims]
            return httpx.Response(200, json={"data": {"type": "metric-aggregate", "attributes": {"dates": dates, "data": rows}}})
        if path in ("/api/flow-values-reports", "/api/campaign-values-reports", "/api/form-values-reports"):
            if self.reports_fail:
                return httpx.Response(400, json={"errors": [{"detail": "report rejected"}]})
            kind = path.split("/")[2].split("-")[0]
            results = [{"groupings": {f"{kind}_id": f"{kind}-1", "send_channel": "email"},
                        "statistics": {"recipients": 100, "conversion_value": 250.0, "delivered": 99}}]
            return httpx.Response(200, json={"data": {"type": f"{kind}-values-report", "attributes": {"results": results}}})
        if path == "/api/events":
            self.event_pages += 1
            if self.crash_on and self.event_pages == self.crash_on:
                raise Crash()
            cursor = int(params.get("page[cursor]", 0))
            if cursor == 0:
                flt = params["filter"]
                assert 'equals(metric_id,"PO1")' in flt and params["fields[event]"] == "datetime,event_properties"
                self.since = flt.split("greater-or-equal(datetime,")[1].split(")")[0]
            rows = [e for e in ORDERS if e["attributes"]["datetime"] >= self.since.replace("Z", "+00:00")]
            chunk = rows[cursor:cursor + self.page]
            nxt = (f"https://a.klaviyo.com/api/events/?page%5Bcursor%5D={cursor + self.page}"
                   if cursor + self.page < len(rows) else None)
            return self._ok(chunk, nxt=nxt)
        return httpx.Response(404, json={"errors": [{"detail": f"unexpected {path}"}]})


@pytest.fixture()
def shop(session, world, vault):
    admin = world.user(world.admin)
    conn = connections.add(session, admin, vault, provider="klaviyo", external_account_id="ACC1", secret=KEY)
    session.commit()
    return world, conn


def _run(session, world, conn, fake=None, **kw):
    fake = fake or FakeKlaviyo()
    scope = world.agent("analyst")
    run = collector.start(session, world.user(world.admin), conn, started_by="test", now=NOW)
    session.commit()
    collector.collect(session, scope, run, fake.client(), now=NOW, sleep=lambda s: None, **kw)
    return run, fake


# ---------------------------------------------------------------- the whole run

def test_full_run_produces_every_snapshot_file_and_the_engine_scores_it(session, shop):
    world, conn = shop
    run, fake = _run(session, world, conn)
    assert run.status == "ready", run.stages
    assert all(s["status"] == "done" for s in run.stages.values())
    snap = collector.snapshot_for(session, run)
    assert snap.missing("snapshot") == []
    assert snap.timezone_name == "America/New_York"
    assert snap.metric_choice("placed_order")["id"] == "PO1"
    assert "dormant" in snap.at("totals", "metricChoices", "placed_order", "reason")
    assert snap.at("totals", "activity") == {"PO1": 120, "WOO": 0}
    assert len(snap.order_history().by_profile) == 12 and snap.order_history().orders == 30
    assert snap.at("templates", "overGmailClipThreshold") == 1  # T2 is over 102,400 bytes; T4 is in a draft flow
    assert {t["templateId"] for t in snap.at("templates", "templates")} == {"T1", "T2", "T3"}

    results = evaluate(snap, "snapshot")
    assert not [k for k, r in results.items() if r.verdict == "error"], {k: r.message for k, r in results.items() if r.verdict == "error"}
    assert results["DAT-05"].verdict == "pass", results["DAT-05"].message
    assert results["CRV-01"].verdict in ("warn", "fail")
    assert results["DAT-01"].verdict == "warn" and "dormant" in results["DAT-01"].message
    assert results["DAT-01"].value["dormant_candidates"] == ["WOO"]

    first = run.stages["first_look"]["summary"]
    assert first["preliminary"] and first["flows"] == {
        "total": 3, "live": 2, "live_by_trigger": {"Added to List": 1, "Metric": 1}, "core_by_name": first["flows"]["core_by_name"]}
    assert first["revenue_12m"]["total"] == 360.0 and first["revenue_12m"]["email"] == 120.0
    assert run.stages["groups"]["segments_counted"] == 2


def test_reporting_calls_are_counted_and_within_budget(session, shop):
    world, conn = shop
    run, fake = _run(session, world, conn)
    reports = [c for c in fake.calls if c[1].endswith("-reports")]
    assert len(reports) == budget.SNAPSHOT_REPORT_CALLS == budget.used(session, "ACC1")  # spent on today's real date
    assert not any("flow-series" in c[1] or "/api/coupons" in c[1] or "/api/catalog" in c[1] or "/api/profiles" in c[1]
                   for c in fake.calls)
    assert not any("/flow-actions" in c[1] or "/flow-messages" in c[1] for c in fake.calls)


def test_no_personal_data_reaches_artifacts_or_orders(session, shop):
    world, conn = shop
    run, _ = _run(session, world, conn)
    blob = json.dumps([a.payload for a in session.scalars(select(m.AuditArtifact).where(m.AuditArtifact.run_id == run.id))])
    blob += json.dumps(collector.snapshot_for(session, run).load("orders"))
    assert not EMAIL.findall(blob) and PRIVATE not in blob
    order = session.scalar(select(m.AuditOrder).where(m.AuditOrder.event_id == "evt000"))
    assert order.items == ["Serum"] and order.discount == ["WELCOME10"] and order.profile_id == "P00"


# ---------------------------------------------------------------- resuming and re-auditing

def test_a_crash_mid_orders_resumes_from_the_checkpoint(session, shop, monkeypatch):
    monkeypatch.setattr(stages, "CHECKPOINT_EVERY", 2)
    world, conn = shop
    fake = FakeKlaviyo(crash_on_event_page=4)
    scope = world.agent("analyst")
    run = collector.start(session, world.user(world.admin), conn, started_by="test", now=NOW)
    session.commit()
    with pytest.raises(Crash):
        collector.collect(session, scope, run, fake.client(), now=NOW, sleep=lambda s: None)
    session.rollback()
    session.refresh(run)
    assert run.stages["orders"]["status"] == "running" and run.stages["orders"]["pages"] == 2
    assert run.stages["totals"]["status"] == "done"
    stored = session.scalar(select(func.count()).select_from(m.AuditOrder))
    assert stored == 10

    again = FakeKlaviyo()
    again.since = fake.since
    collector.collect(session, scope, run, again.client(), now=NOW, sleep=lambda s: None)
    assert run.status == "ready"
    cursors = [c[2].get("page[cursor]") for c in again.calls if c[1] == "/api/events"]
    assert cursors[0] == "10"  # picked up after the last checkpoint, not from page one
    assert not [c for c in again.calls if c[1] in ("/api/flows", "/api/metric-aggregates")]  # finished stages aren't re-read
    assert session.scalar(select(func.count()).select_from(m.AuditOrder)) == 30
    assert collector.snapshot_for(session, run).order_history().duplicates == 0


def test_a_re_audit_reads_only_new_orders(session, shop):
    world, conn = shop
    first, _ = _run(session, world, conn)
    later = NOW + timedelta(days=30)
    fake = FakeKlaviyo()
    run = collector.start(session, world.user(world.admin), conn, started_by="test", now=later)
    session.commit()
    collector.collect(session, world.agent("analyst"), run, fake.client(), now=later, sleep=lambda s: None)
    assert run.status == "ready"
    assert fake.since.startswith((NOW - stages.ORDER_OVERLAP).strftime("%Y-%m-%d"))
    assert run.stages["orders"]["since"] != run.stages["orders"]["start"]
    window_start = datetime.fromisoformat(run.stages["orders"]["start"].replace("Z", "+00:00"))
    in_window = sum(1 for e in ORDERS if datetime.fromisoformat(e["attributes"]["datetime"]) >= window_start)
    assert in_window < len(ORDERS)  # the oldest orders fell out of the later 24-month window
    assert collector.snapshot_for(session, run).order_history().orders == in_window


def test_start_returns_the_active_run_instead_of_a_second_one(session, shop):
    world, conn = shop
    a = collector.start(session, world.user(world.admin), conn, started_by="test", now=NOW)
    b = collector.start(session, world.user(world.admin), conn, started_by="test", now=NOW)
    assert a.id == b.id
    assert session.scalar(select(func.count()).select_from(m.JobRun).where(m.JobRun.job_key == "audit.collect")) == 1


# ---------------------------------------------------------------- failures stay contained

def test_wrong_account_fails_the_run_before_reading_anything(session, shop):
    world, conn = shop
    run, fake = _run(session, world, conn, FakeKlaviyo(account="SOMEONE_ELSE"))
    assert run.status == "failed" and "SOMEONE_ELSE" in run.error
    assert {c[1] for c in fake.calls} == {"/api/accounts"}
    assert all(s["status"] == "skipped" for k, s in run.stages.items() if k != "verify")


def test_a_broken_pin_stops_revenue_stages_and_says_why(session, shop):
    world, conn = shop
    session.add(m.MetricPin(org_id=world.org.id, client_id=world.a.id, name="Placed Order", metric_id="GONE",
                            integration=None, created_by="test"))
    session.commit()
    run, fake = _run(session, world, conn, pins=collector.pins_for(session, world.a.id))
    assert run.status == "partial"
    assert run.stages["first_look"]["status"] == "failed" and "GONE" in run.stages["first_look"]["error"]
    assert {k for k, s in run.stages.items() if s["status"] == "skipped"} == {
        "structure", "performance", "templates", "totals", "orders", "groups"}
    assert not [c for c in fake.calls if c[1].endswith("-reports")]


def test_failed_reports_are_recorded_and_flagged_not_fatal(session, shop):
    world, conn = shop
    run, _ = _run(session, world, conn, FakeKlaviyo(reports_fail=True))
    assert run.status == "ready"  # every stage finished; DAT-05 reports the gaps
    snap = collector.snapshot_for(session, run)
    assert snap.at("perf", "reports", "flow90", "ok") is False
    results = evaluate(snap, "snapshot")
    assert results["DAT-05"].verdict == "fail"
    assert results["FLW-08"].verdict == "na"


# ---------------------------------------------------------------- the reporting budget

def test_budget_refuses_the_41st_call_and_a_run_that_would_cross_it(session, shop):
    world, conn = shop
    budget.spend(session, "ACC1", 36, now=NOW)
    with pytest.raises(budget.BudgetExceeded):
        collector.start(session, world.user(world.admin), conn, started_by="test", now=NOW)
    budget.spend(session, "ACC1", 4, now=NOW)
    with pytest.raises(budget.BudgetExceeded):
        budget.spend(session, "ACC1", now=NOW)
    assert budget.used(session, "ACC1", NOW) == 40
    assert budget.used(session, "ACC1", NOW + timedelta(days=1)) == 0
    assert budget.spend(session, "ACC1", now=NOW + timedelta(days=1)) == 1


def test_reports_stop_at_the_budget_mid_run(session, shop):
    world, conn = shop
    run, fake = _run(session, world, conn, report_limit=2)
    assert len([c for c in fake.calls if c[1].endswith("-reports")]) == 2
    perf = collector.snapshot_for(session, run).load("perf")
    assert perf["reports"]["flow365"]["ok"] and not perf["reports"]["flow90"]["ok"]
    assert "budget" in perf["reports"]["flow90"]["error"]["message"]


# ---------------------------------------------------------------- through the job queue

def test_the_job_runs_the_audit_with_the_vaulted_key(factory, session, shop, vault):
    world, conn = shop
    run = collector.start(session, world.user(world.admin), conn, started_by="test", now=NOW)
    session.commit()
    fake = FakeKlaviyo()
    done = jobs.run_pending(factory, {"vault": vault, "klaviyo_factory": lambda key: KlaviyoClient(
        key, transport=fake.transport(), pace=False, sleep=lambda s: None), "sleep": lambda s: None})
    assert [(j.job_key, j.status) for j in done] == [("audit.collect", "succeeded"), ("audit.analyze", "succeeded"),
                                                     ("outcomes.readout", "no_action"),
                                                     ("narrative.draft", "no_action")], [j.error for j in done]
    assert "No model provider is allowed" in done[-1].summary
    session.expire_all()
    run = session.get(m.AuditRun, run.id)
    assert run.status == "ready" and run.scorecard_id
    summary = session.get(m.Scorecard, run.scorecard_id).summary
    assert summary["audit_run_id"] == run.id and summary["opportunities"][0]["check_id"] == "DAT-01"
    errors = {k: v["reason"] for k, v in summary["analysis"].items() if v["status"] == "error"}
    assert not errors, errors
    assert session.scalar(select(m.AuditFact.id).where(m.AuditFact.run_id == run.id)) is not None
    again = jobs.run_pending(factory, {"vault": vault, "klaviyo_factory": lambda key: fake.client()})
    assert again == []
    actions = list(session.scalars(select(m.Event.action).where(m.Event.client_id == world.a.id)))
    assert "audit.started" in actions and "audit.ready" in actions and "connection.used" in actions
