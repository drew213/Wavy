"""Phase 6: cost guards and hosting signals. Klaviyo event pages have their own per-account budget, model spend
is capped per org per day, jobs are limited per family, storage per brand shows in /healthz and the staff scale
view, and the scale check says when to move hosting up a step (synthetic data only)."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from wavyos import dbsize, jobs, operations, scale, spend
from wavyos import models as m
from wavyos.collector import budget
from wavyos.collector import run as collector
from wavyos.db import utcnow
from wavyos.execution import engine
from wavyos.execution.adapters import FakeAdapter
from wavyos.narrative.prompts import SECTIONS
from wavyos.narrative.writers import FakeWriter
from wavyos.scope import InvalidState

from test_collector import NOW, FakeKlaviyo, _run, shop  # noqa: F401
from test_execution import PRODUCE, _setup, qa
from test_klaviyo_onboarding import env, fake_klaviyo  # noqa: F401
from test_narrative import _brand_with_audit, critic, drafter


# ---------------------------------------------------------------- Klaviyo event pages

def test_event_pages_have_their_own_budget_and_stop_the_order_read(session, shop):
    world, conn = shop
    limit = budget.LIMITS["events"]
    budget.spend(session, "ACC1", 10)
    budget.spend(session, "ACC1", limit - 2, family="events")
    assert budget.used(session, "ACC1") == 10 and budget.used(session, "ACC1", family="events") == limit - 2
    assert budget.today(session, ["ACC1", "ACC2"]) == {"ACC1": {"reporting": 10, "events": limit - 2},
                                                       "ACC2": {"reporting": 0, "events": 0}}

    run, fake = _run(session, world, conn)  # 30 orders at 5 a page would take 6 pages; 2 are left today
    assert fake.event_pages == 2
    orders = run.stages["orders"]
    assert "events budget" in orders["error"]["message"] and orders["stored"] == 10
    assert session.scalar(select(func.count()).select_from(m.AuditOrder)) == 10
    assert budget.used(session, "ACC1", family="events") == limit
    with pytest.raises(budget.BudgetExceeded):
        budget.spend(session, "ACC1", family="events")
    with pytest.raises(ValueError):
        budget.spend(session, "ACC1", family="profiles")


def test_an_audit_is_refused_once_the_event_pages_are_used(session, shop):
    world, conn = shop
    budget.spend(session, "ACC1", budget.LIMITS["events"], family="events", now=NOW)
    with pytest.raises(budget.BudgetExceeded, match="event pages"):
        collector.start(session, world.user(world.admin), conn, started_by="test", now=NOW)
    assert budget.used(session, "ACC1", NOW) == 0  # the reporting budget is untouched


# ---------------------------------------------------------------- model spend per org per day

def test_the_agency_cap_stops_drafting_across_brands(env, monkeypatch):
    factory, staff, _, _ = env
    monkeypatch.setenv("WAVYOS_ORG_LLM_DAILY_USD", "3")
    writers = {"anthropic": FakeWriter(drafter(), cost_usd=1.0), "codex": FakeWriter(critic(), cost_usd=1.0)}
    _, slug, done = _brand_with_audit(env, providers=["anthropic", "codex"], writers=writers,
                                      settings={"audit_llm_cap_usd": 100.0})
    status = {d["section"]: d for d in staff.get(f"/v1/ops/narrative?client={slug}").json()["drafts"]}
    first = next(iter(SECTIONS))
    assert status[first]["status"] == "needs_rewrite" and "agency's $3.00 daily spend cap" in status[first]["reason"]
    assert all(d["status"] == "skipped" and "for the agency" in d["reason"] for k, d in status.items() if k != first)
    assert done["narrative.draft"].status == "succeeded" and "$2.00 spent" in done["narrative.draft"].summary
    with factory() as s:
        org_id = s.scalar(select(m.Client.org_id).where(m.Client.slug == slug))
        assert spend.used(s, org_id) == 2.0
        row = s.scalar(select(m.LlmSpend).where(m.LlmSpend.org_id == org_id))
        assert row.calls == 2
    morning = staff.get("/v1/ops/morning").json()
    assert any("spend cap" in b["reason"] for b in morning["blocked"])


class CostlyAdapter(FakeAdapter):
    def run(self, packet, tools, gw, model, effort):
        out = super().run(packet, tools, gw, model, effort)
        out.cost_usd = 0.75
        return out


def test_executions_charge_the_org_and_stop_at_its_cap(factory, session, world, monkeypatch):
    w = world
    t = _setup(session, w)
    engine.dispatch(session, w.agent("strategist"), t.id, executor="codex")
    session.commit()
    engine.work(factory, {"codex": CostlyAdapter(PRODUCE), "anthropic": FakeAdapter(qa({}))})
    with factory() as s:
        assert spend.used(s, w.org.id) == 0.75
    monkeypatch.setenv("WAVYOS_ORG_LLM_DAILY_USD", "0.75")
    session.expire_all()
    t2 = operations.create_task(session, w.agent("strategist"), decision_id=t.decision_id, title="Email 2",
                                area="voice_copy", owner_role="creative", done_when="x", checklist=["y"])
    with pytest.raises(InvalidState, match="for the agency"):
        engine.dispatch(session, w.agent("strategist"), t2.id)
    monkeypatch.setenv("WAVYOS_ORG_LLM_DAILY_USD", "not a number")
    assert spend.daily_cap() == spend.DEFAULT_ORG_DAILY_USD


# ---------------------------------------------------------------- jobs per family

def test_a_family_at_its_limit_waits_and_others_still_run(factory, session, world, monkeypatch):
    session.commit()
    monkeypatch.setenv("WAVYOS_JOB_FAMILY_LIMITS", "llm=1, bogus, klaviyo=x")
    assert jobs.family_limits() == {**jobs.FAMILY_LIMITS, "llm": 1}
    assert jobs.REGISTRY["narrative.draft"].family == "llm" and jobs.REGISTRY["audit.collect"].family == "klaviyo"
    with factory() as s:
        c = s.get(m.Client, world.a.id)
        past = utcnow() - timedelta(minutes=5)
        busy = jobs._insert(s, c, jobs.REGISTRY["narrative.draft"], "busy", past, {})
        busy.status = "running"
        waiting = jobs._insert(s, c, jobs.REGISTRY["narrative.draft"], "waiting", past, {})
        other = jobs._insert(s, c, jobs.REGISTRY["outcomes.readout"], "other", past + timedelta(minutes=1), {})
        s.commit()
        busy_id, waiting_id, other_id = busy.id, waiting.id, other.id
    with factory() as s:
        assert jobs.saturated(s) == ["narrative.draft"]
    assert jobs.claim(factory) == other_id  # the older llm run waits; the analysis run goes ahead
    assert jobs.claim(factory) is None
    with factory() as s:
        s.get(m.JobRun, busy_id).status = "succeeded"
        s.commit()
    assert jobs.claim(factory) == waiting_id


# ---------------------------------------------------------------- storage and the hosting trigger

def test_healthz_shows_totals_and_the_scale_view_names_brands(env):
    factory, staff, vault, _ = env
    brand, slug, _ = _brand_with_audit(env, providers=[], writers={})
    health = staff.get("/healthz").json()
    assert health["status"] == "ok" and health["storage"]["brands"] >= 1 and "database_mb" in health["storage"]
    assert slug not in json.dumps(health)  # public: totals only

    view = staff.get("/v1/ops/scale?fresh=true").json()
    names = {b["slug"] for b in view["storage"]["brands"]}
    assert slug in names and all(b["rows"] > 0 for b in view["storage"]["brands"])
    assert {f["family"] for f in view["families"]} >= {"klaviyo", "analysis", "llm"}
    k = next(x for x in view["klaviyo"] if x["client"] == slug)
    assert k["reporting"]["used"] == budget.SNAPSHOT_REPORT_CALLS and k["events"]["used"] >= 1
    assert view["llm"]["cap_usd"] == spend.daily_cap()
    assert view["signals"]["move"] is False, view["signals"]
    assert brand.get("/v1/ops/scale").status_code == 404

    with factory() as s:
        s.scalar(select(m.Client).where(m.Client.slug == slug)).status = "active"
        s.commit()
    sig = staff.get("/v1/ops/scale").json()["signals"]
    assert sig["move"] and sig["step"] == "step1" and "marked active" in sig["reasons"][0]


def test_scale_signals_follow_storage_lag_and_hosting(factory, session, world):
    session.commit()
    small = {"database_mb": 40, "used_share": 0.08, "free_plan_mb": dbsize.FREE_PLAN_MB}
    assert scale.signals(session, small, embedded_worker=True)["move"] is False
    full = {**small, "database_mb": 400, "used_share": 0.78}
    sig = scale.signals(session, full, embedded_worker=True)
    assert sig["step"] == "step1" and "78%" in sig["reasons"][0] and "Step 1" in sig["action"]
    assert scale.signals(session, full, embedded_worker=False)["move"] is False  # already on a paid database

    c = session.get(m.Client, world.a.id)
    jobs._insert(session, c, jobs.REGISTRY["audit.refresh"], "late", utcnow() - timedelta(hours=3), {})
    session.commit()
    sig = scale.signals(session, small, embedded_worker=False)
    assert sig["step"] == "threads" and sig["metrics"]["missed_overnight_runs"] == 1
    assert sig["metrics"]["queue_lag_minutes"] >= 179
    big = scale.signals(session, {**small, "database_mb": 8000}, embedded_worker=False)
    assert big["step"] == "step2" and "object storage" in big["action"]


def test_storage_is_measured_once_per_ttl(factory, session, world):
    session.commit()
    first = dbsize.cached(factory)
    assert dbsize.cached(factory) is first
    assert dbsize.cached(factory, force=True) is not first
    assert dbsize.summary({"error": "x"}) == {"error": "x"}
    mine = dbsize.for_clients({"brands": [{"client_id": "a"}, {"client_id": "b"}]}, {"a"})
    assert [b["client_id"] for b in mine["brands"]] == ["a"]
