"""Phase 5: the loop after the first audit. Weekly incremental refresh and monthly full re-audit in the
overnight batch, What changed, the monthly report, outcomes of approved opportunities feeding ranking,
the morning queue and the learning report (synthetic data only)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from wavyos import jobs
from wavyos import models as m
from wavyos.analysis import flow_categories
from wavyos.analysis.base import Analysis
from wavyos.audit import opportunities
from wavyos.loop.changes import lower_is_better

from test_klaviyo_onboarding import _services, env, fake_klaviyo  # noqa: F401
from test_narrative import _brand_with_audit


def _section(bundle: dict, sid: str) -> dict:
    return next(s for s in bundle["sections"] if s["id"] == sid)


def _run_job(factory, vault, slug: str, key: str) -> dict[str, m.JobRun]:
    with factory() as s:
        c = s.scalar(select(m.Client).where(m.Client.slug == slug))
        jobs._insert(s, c, jobs.REGISTRY[key], f"{key}:{c.id}:test:{datetime.now().timestamp()}",
                     datetime.now(timezone.utc) - timedelta(minutes=1), {"kind": "schedule", "period": "test"})
        s.commit()
    done = jobs.run_pending(factory, {**_services(vault, httpx.MockTransport(fake_klaviyo())), "writers": {}}, limit=12)
    return {j.job_key: j for j in done}


def _age_runs(factory, slug: str, days: int) -> None:
    with factory() as s:
        c = s.scalar(select(m.Client).where(m.Client.slug == slug))
        for r in s.scalars(select(m.AuditRun).where(m.AuditRun.client_id == c.id)):
            r.created_at = r.created_at - timedelta(days=days)
        s.commit()


# ---------------------------------------------------------------- scheduling

def test_overnight_jobs_are_due_at_two_and_held_until_then(factory, session, world):
    session.commit()
    tz = "America/New_York"
    early = datetime(2026, 3, 2, 5, 10, tzinfo=timezone.utc)  # 00:10 in New York
    assert jobs.due_time(early, tz, 2) == datetime(2026, 3, 2, 7, 0, tzinfo=timezone.utc)
    late = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
    assert jobs.due_time(late, tz, 2) == late and jobs.due_time(early, tz, None) == early
    with factory() as s:
        c = s.get(m.Client, world.a.id)
        held = jobs._insert(s, c, jobs.REGISTRY["audit.refresh"], "held", datetime.now(timezone.utc) + timedelta(hours=3), {})
        s.commit()
        held_id = held.id
    assert jobs.claim(factory, "audit.refresh") is None
    with factory() as s:
        assert s.get(m.JobRun, held_id).status == "queued"
    assert jobs.REGISTRY["monthly.report"].hour == jobs.REGISTRY["audit.refresh"].hour == 2


# ---------------------------------------------------------------- the loop end to end

def test_refresh_compares_reads_out_and_feeds_the_morning_queue(env):
    factory, staff, vault, _ = env
    brand, slug, done = _brand_with_audit(env, providers=[], writers={})
    assert done["audit.analyze"].status == "succeeded", done["audit.analyze"].error
    first = staff.get(f"/v1/ops/clients/{slug}/workspace").json()
    changes = _section(first, "changes")
    assert changes["state"] == "unavailable" and "first scored audit" in changes["reason"]
    report = _section(first, "report")
    assert report["narrative_state"] == "awaiting_review" and report["data"]["top"]
    cats = report["tables"]["flow_categories.categories"]
    assert [r["category"] for r in cats][:6] == ["welcome", "checkout", "cart", "browse", "cross_sell", "replenishment"]
    assert all(f"flow_categories.{r['category']}.live_flows" in first["facts"] for r in cats)

    # a strategist approves the top card, and it can't be approved twice
    cards = staff.get(f"/v1/ops/clients/{slug}/outcomes").json()["opportunities"]
    card = cards[0]
    res = staff.post(f"/v1/ops/clients/{slug}/opportunities/{card['id']}/approve", json={"owner": "Wavy Studios"})
    assert res.status_code == 200, res.text
    outcome = res.json()
    assert outcome["status"] == "tracking" and outcome["owner"] == "Wavy Studios"
    assert staff.post(f"/v1/ops/clients/{slug}/opportunities/{card['id']}/approve",
                      json={"owner": "Wavy Studios"}).status_code == 409
    assert staff.post(f"/v1/ops/clients/{slug}/opportunities/opp-nope/approve", json={"owner": "x"}).status_code == 404
    opp = _section(staff.get(f"/v1/ops/clients/{slug}/workspace").json(), "opportunities")
    approved = next(c for c in opp["data"]["opportunities"] if c["id"] == card["id"])
    assert approved["status"] == "Approved" and approved["owner"] == "Wavy Studios"

    # a week later, with the readout window passed, the overnight refresh runs
    _age_runs(factory, slug, 8)
    with factory() as s:
        o = s.get(m.OpportunityOutcome, outcome["id"])
        o.approved_at, o.due_at = o.approved_at - timedelta(days=60), datetime.now(timezone.utc) - timedelta(days=1)
        s.commit()
    done = _run_job(factory, vault, slug, "audit.refresh")
    assert done["audit.refresh"].status == "succeeded", (done["audit.refresh"].summary, done["audit.refresh"].error)
    assert done["audit.analyze"].status == "succeeded", done["audit.analyze"].error
    assert done["outcomes.readout"].status == "succeeded", done["outcomes.readout"].error
    with factory() as s:
        c = s.scalar(select(m.Client).where(m.Client.slug == slug))
        latest = s.scalar(select(m.AuditRun).where(m.AuditRun.client_id == c.id).order_by(m.AuditRun.created_at.desc()))
        orders = latest.stages["orders"]
        assert latest.context["refresh"] == "incremental" and orders["since"] > orders["start"]  # only new orders
        o = s.get(m.OpportunityOutcome, outcome["id"])
        assert o.status in ("improved", "flat", "worse", "unmeasured") and o.readouts and o.verdict_reason

    second = staff.get(f"/v1/ops/clients/{slug}/workspace").json()
    assert second["version"] == 2
    changes = _section(second, "changes")
    assert changes["state"] == "ready", changes["reason"]
    assert "changes.days_between" in second["facts"] and "changes.checks_regressed" in second["facts"]
    assert changes["tables"]["changes.summary"][0]["same_library"] is True
    assert {r["change"] for r in changes["tables"]["changes.opportunities"]} <= {"new", "still_open", "resolved"}
    assert _section(second, "report")["data"]["refresh"] == "incremental"

    morning = staff.get("/v1/ops/morning").json()
    mine = [r for r in morning["changed"] if r["client"] == slug]
    assert mine and mine[0]["refresh"] == "incremental" and mine[0]["first_audit"] is False
    assert any(r["id"] == outcome["id"] for r in morning["outcomes_decided"])
    assert brand.get("/v1/ops/morning").status_code == 404 and brand.get("/v1/ops/learning").status_code == 404

    learning = staff.get("/v1/ops/learning").json()
    play = next(p for p in learning["playbooks"] if p["source_key"] == outcome["source_key"])
    assert play["approved"] == 1 and play[o.status] == 1

    # the monthly re-audit re-reads the whole order window
    _age_runs(factory, slug, 3)
    done = _run_job(factory, vault, slug, "monthly.report")
    assert done["monthly.report"].status == "succeeded", done["monthly.report"].summary
    with factory() as s:
        c = s.scalar(select(m.Client).where(m.Client.slug == slug))
        latest = s.scalar(select(m.AuditRun).where(m.AuditRun.client_id == c.id).order_by(m.AuditRun.created_at.desc()))
        assert latest.context["refresh"] == "full" and latest.stages["orders"]["since"] == latest.stages["orders"]["start"]
    again = _run_job(factory, vault, slug, "audit.refresh")
    assert again["audit.refresh"].status == "no_action" and "in the last 5 days" in again["audit.refresh"].summary


def test_a_brand_with_no_audit_gets_no_refresh(factory, session, world):
    session.commit()
    with factory() as s:
        c = s.get(m.Client, world.a.id)
        jobs._insert(s, c, jobs.REGISTRY["audit.refresh"], "first", datetime.now(timezone.utc), {})
        s.commit()
    done = jobs.run_pending(factory, {})
    run = next(j for j in done if j.job_key == "audit.refresh")
    assert run.status == "no_action"


# ---------------------------------------------------------------- pieces

def test_track_record_moves_a_playbook_up_or_down():
    assert opportunities.confidence(None) == 1.0
    worked = {"improved": 4, "flat": 0, "worse": 0}
    failed = {"improved": 0, "flat": 1, "worse": 3}
    assert opportunities.confidence(worked) > 1.0 > opportunities.confidence(failed) >= 0.5
    base = {"category": "flow_build", "effort_days": 2, "severity": "medium", "verdict": "warn",
            "sizing": {"status": "sized", "annual": {"base": 10000}}}
    a, b = {**base, "id": "a", "track_record": failed}, {**base, "id": "b", "track_record": worked}
    assert sorted([a, b], key=opportunities._rank_key)[0]["id"] == "b"


def test_flow_categories_merge_third_party_twins_and_keep_the_rest():
    assert flow_categories.category_of("abandoned_cart", "Cart") == "cart"
    assert flow_categories.category_of(None, "Retention.com Abandoned Checkout") == "checkout"
    assert flow_categories.category_of(None, "Mystery") == "other"
    assert flow_categories.third_party("Retention.com Abandoned Cart") and not flow_categories.third_party("Cart Recovery")
    a = Analysis(flow_categories.MODULE)
    table = [{"category": "cart", "label": "Cart abandonment", "live_flows": 2, "revenue": 900.0,
              "third_party_revenue": 300.0, "third_party_share": 1 / 3,
              "flows": [{"name": "Cart", "live": True, "third_party": False, "recipients": 100, "revenue": 600.0},
                        {"name": "RC Cart", "live": True, "third_party": True, "recipients": 80, "revenue": 300.0},
                        {"name": "Cart SMS", "live": True, "third_party": False, "recipients": 50, "revenue": 0.0}]}]
    flow_categories._findings(a, table, measured=True)
    ids = {f.id for f in a.findings}
    assert ids == {"flow_categories.cart_twin", "flow_categories.cart_no_revenue"}
    assert all(f.action and f.evidence for f in a.findings)


def test_direction_of_a_metric():
    assert lower_is_better("complaints.spam_rate_90d") and lower_is_better("x", "Unsubscribe rate")
    assert not lower_is_better("performance.store_revenue_month", "Store revenue")


# ---------------------------------------------------------------- the eval harness (synthetic folder only)

def test_the_eval_harness_scores_a_saved_folder(tmp_path, monkeypatch):
    import importlib.util
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("eval_audit", root / "scripts" / "eval_audit.py")
    ev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ev)
    monkeypatch.setattr(ev, "OUT", tmp_path)
    folder = str(root / "tests" / "fixtures" / "audit" / "synthetic-shop")
    out = ev.run_engine(folder)
    cards = out["built"]["opportunities"]
    fid = next(k for k, f in out["facts"].items() if isinstance(f["value"], (int, float)) and f["value"])
    expect = {"name": "synthetic", "folder": folder, "top_n": 5,
              "opportunities": [{"id": cards[0]["id"]}, {"title": "Nothing like this exists", "match": ["zzz-never"]}],
              "findings": [{"title": cards[-1]["title"], "match": [cards[-1]["title"]]}],
              "numbers": [{"fact": fid, "expected": out["facts"][fid]["value"] * 1.001, "tolerance": 0.01},
                          {"fact": fid, "expected": out["facts"][fid]["value"] * 2, "tolerance": 0.01},
                          {"fact": "nope.missing", "expected": 1}]}
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(expect))
    scores = ev.run_file(path)
    assert scores["top_overlap"] == 0.5 and scores["headline_recall"] == 1.0 and scores["number_parity"] == 0.5
    assert scores["cards_complete"] == 1.0
    report = (tmp_path / "synthetic-report.md").read_text()
    assert "MISSED: Nothing like this exists" in report and "## Not measured" in report
