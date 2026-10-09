from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

import wavyos.jobmods  # noqa: F401  (registers the jobs)
from wavyos import approvals, connections, jobs, knowledge, operations
from wavyos import models as m
from wavyos.connectors.klaviyo import KlaviyoClient, KlaviyoError
from wavyos.jobmods import CONTRACT_ONLY, IMPLEMENTED

DAY1 = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
SECRET = "pk_test_jobmods_0000000000000000"


def runs_for(factory, w, now, services, job_key):
    """Queue whatever is due at `now`, run it all, return this job's runs by client id."""
    with factory() as s:
        jobs.enqueue_due(s, now=now, org_id=w.org.id)
        s.commit()
    done = [r for r in jobs.run_pending(factory, services) if r.job_key == job_key]
    by_client = {r.client_id: r for r in done}
    assert len(by_client) == len(done)
    return by_client


def run_day(factory, w, now, services, job_key):
    """The run for client A."""
    return runs_for(factory, w, now, services, job_key)[w.a.id]


class FakeKlaviyo:
    """Stands in for KlaviyoClient: only the read methods the job uses exist."""

    def __init__(self, world, account_id="AbC123"):
        self.world, self.account_id, self.keys, self.closed = world, account_id, [], 0

    def __call__(self, api_key):  # the factory
        self.keys.append(api_key)
        return self

    def account(self):
        return {"id": self.account_id, "name": "Brand A", "timezone": "America/Los_Angeles"}

    def flows(self):
        return [{"type": "flow", "id": fid, "attributes": {"name": name, "status": status, "archived": False}}
                for fid, (name, status) in self.world.items()]

    def close(self):
        self.closed += 1


@pytest.fixture()
def connected(session, world, vault):
    c = connections.add(session, world.user(world.lead), vault, provider="klaviyo", external_account_id="AbC123",
                        secret=SECRET, label="Brand A Klaviyo")
    session.commit()
    return c


def insights(factory, client_id):
    with factory() as s:
        return list(s.scalars(select(m.Insight).where(m.Insight.client_id == client_id).order_by(m.Insight.created_at)))


def flow_rows(factory, client_id, flow_id):
    with factory() as s:
        return list(s.scalars(select(m.MetricSnapshot).where(
            m.MetricSnapshot.client_id == client_id, m.MetricSnapshot.metric_key == f"flow_status:{flow_id}")
            .order_by(m.MetricSnapshot.period_start)))


# ---------------------------------------------------------------- account.daily_health

def test_daily_health_baseline_then_change_then_no_duplicate(factory, world, vault, connected):
    w = world
    state = {"F1": ("Welcome", "live"), "F2": ("Abandoned cart", "live"), "F3": ("Winback", "draft")}
    fake = FakeKlaviyo(state)
    services = {"vault": vault, "klaviyo_factory": fake}

    services["today"] = date(2026, 10, 9)
    runs = runs_for(factory, w, DAY1, services, "account.daily_health")
    run, other = runs[w.a.id], runs[w.b.id]
    assert run.status == "no_action" and "baseline recorded" in run.summary
    assert other.status == "no_action" and "no Klaviyo connection" in other.summary  # B has no connection
    assert fake.keys == [SECRET] and fake.closed == 1
    assert len(flow_rows(factory, w.a.id, "F1")) == 1 and insights(factory, w.a.id) == []

    # day 2: the welcome flow drops to draft; winback going live is not a problem
    state["F1"], state["F3"] = ("Welcome", "draft"), ("Winback", "live")
    services["today"] = date(2026, 10, 10)
    run = run_day(factory, w, DAY1 + timedelta(days=1), services, "account.daily_health")
    assert run.status == "succeeded", run.error
    found = insights(factory, w.a.id)
    assert [i.dedupe_key for i in found] == ["flow_status:F1:draft"]
    assert run.produced["insights"] == [found[0].id]
    assert found[0].area == "flow_audit" and found[0].source_ref == f"job_run:{run.id}"
    f1 = flow_rows(factory, w.a.id, "F1")
    assert [r.payload["status"] for r in f1] == ["live", "draft"]
    assert {e["ref"] for e in found[0].evidence} >= {f"metric_snapshot:{f1[0].id}", f"metric_snapshot:{f1[1].id}"}
    f2 = flow_rows(factory, w.a.id, "F2")  # unchanged flows extend their row instead of adding one
    assert len(f2) == 1 and (f2[0].period_start, f2[0].period_end) == (date(2026, 10, 9), date(2026, 10, 10))

    # day 3: still draft: nothing new
    services["today"] = date(2026, 10, 11)
    run = run_day(factory, w, DAY1 + timedelta(days=2), services, "account.daily_health")
    assert run.status == "no_action" and "no flow stopped" in run.summary
    # days 4-5: it flaps back live and down again while the first insight is open: still one insight
    for i, status in ((3, "live"), (4, "draft")):
        state["F1"] = ("Welcome", status)
        services["today"] = date(2026, 10, 9) + timedelta(days=i)
        run_day(factory, w, DAY1 + timedelta(days=i), services, "account.daily_health")
    assert [i.dedupe_key for i in insights(factory, w.a.id)] == ["flow_status:F1:draft"]

    with factory() as s:  # every use of the key was logged with its purpose
        used = s.scalars(select(m.Event).where(m.Event.client_id == w.a.id, m.Event.action == "connection.used")).all()
        assert len(used) == 5 and {e.summary for e in used} == {"daily health"}


def test_daily_health_no_connection(factory, world, vault, session):
    session.commit()
    services = {"vault": vault, "klaviyo_factory": lambda key: pytest.fail("no connection, so no client")}
    run = run_day(factory, world, DAY1, services, "account.daily_health")
    assert run.status == "no_action" and "no Klaviyo connection" in run.summary


def test_daily_health_missing_errored_and_bad_connections(factory, world, vault, connected):
    w = world
    state = {"F1": ("Welcome", "live"), "F2": ("Browse abandon", "live")}
    fake = FakeKlaviyo(state)
    services = {"vault": vault, "klaviyo_factory": fake, "today": date(2026, 10, 9)}
    run_day(factory, w, DAY1, services, "account.daily_health")

    del state["F2"]                          # deleted in Klaviyo
    state["F9"] = ("Post-purchase", "error")  # new flow, already errored
    services["today"] = date(2026, 10, 10)
    run = run_day(factory, w, DAY1 + timedelta(days=1), services, "account.daily_health")
    assert run.status == "succeeded"
    assert sorted(i.dedupe_key for i in insights(factory, w.a.id)) == ["flow_status:F2:missing", "flow_status:F9:error"]

    # the key now opens someone else's account: flag it, don't compare flows across accounts
    fake.account_id = "SomeoneElse"
    services["today"] = date(2026, 10, 11)
    run = run_day(factory, w, DAY1 + timedelta(days=2), services, "account.daily_health")
    assert run.status == "succeeded"
    mismatch = [i for i in insights(factory, w.a.id) if i.dedupe_key == f"klaviyo_account_mismatch:{connected.id}"]
    assert len(mismatch) == 1 and mismatch[0].severity == "critical"

    class Broken:
        def account(self):
            raise KlaviyoError("Klaviyo GET /api/accounts/ failed: HTTP 401: not_authenticated", status=401)

        def flows(self):
            raise AssertionError("unreachable")

    services.update(klaviyo_factory=lambda key: Broken(), today=date(2026, 10, 12))
    run = run_day(factory, w, DAY1 + timedelta(days=3), services, "account.daily_health")
    assert run.status == "succeeded" and "couldn't be read" in run.summary
    services["today"] = date(2026, 10, 13)  # still broken the next day: already flagged, so no_action with the reason
    run = run_day(factory, w, DAY1 + timedelta(days=4), services, "account.daily_health")
    assert run.status == "no_action" and "flagged in open insights" in run.summary


def test_daily_health_through_real_client_is_get_only(factory, world, vault, connected):
    w = world
    seen = []
    flows = {"F1": "live"}

    def handler(r: httpx.Request):
        seen.append((r.method, r.url.path, r.headers["authorization"]))
        if r.url.path == "/api/accounts/":
            return httpx.Response(200, json={"data": [{"type": "account", "id": "AbC123", "attributes": {
                "contact_information": {"organization_name": "Brand A"}, "timezone": "America/Los_Angeles"}}]})
        return httpx.Response(200, json={"data": [{"type": "flow", "id": k, "attributes": {"name": k, "status": v}}
                                                  for k, v in flows.items()], "links": {"next": None}})

    def make(key):
        return KlaviyoClient(key, transport=httpx.MockTransport(handler), sleep=lambda s: None)

    services = {"vault": vault, "klaviyo_factory": make, "today": date(2026, 10, 9)}
    run_day(factory, w, DAY1, services, "account.daily_health")
    flows["F1"] = "manual"
    services["today"] = date(2026, 10, 10)
    run = run_day(factory, w, DAY1 + timedelta(days=1), services, "account.daily_health")
    assert run.status == "succeeded"
    assert {s[0] for s in seen} == {"GET"} and {s[1] for s in seen} == {"/api/accounts/", "/api/flows/"}
    assert {s[2] for s in seen} == {f"Klaviyo-API-Key {SECRET}"}
    assert SECRET not in (run.summary + run.error)


# ---------------------------------------------------------------- knowledge.staleness

def _approved_fact(session, w, key, review_by):
    f, ap = knowledge.propose_fact(session, w.agent("account_lead"), area="offers", component="facts_rules", key=key,
                                   title=f"Fact {key}", source_kind="meeting", source_ref="kickoff", review_by=review_by)
    approvals.decide(session, w.user(w.approver), ap.id, True)
    return f


def test_staleness_opens_one_review_task(factory, world, session, vault):
    w = world
    _approved_fact(session, w, "welcome_offer", date(2026, 9, 1))      # past its review date
    _approved_fact(session, w, "shipping_cutoff", None)                # never given one
    _approved_fact(session, w, "brand_tone", date(2027, 1, 1))         # current
    session.commit()
    services = {"vault": vault, "klaviyo_factory": FakeKlaviyo({}), "today": date(2026, 10, 9)}

    run = run_day(factory, w, DAY1, services, "knowledge.staleness")
    assert run.status == "succeeded", run.error
    with factory() as s:
        (task,) = s.scalars(select(m.Task).where(m.Task.client_id == w.a.id)).all()
        assert run.produced["tasks"] == [task.id] and task.job_run_id == run.id
        assert (task.owner_role, task.area, task.due_date) == ("account_lead", "knowledge_maintenance", date(2026, 10, 16))
        texts = [c["text"] for c in task.checklist]
        assert len(texts) == 2 and "[welcome_offer" in texts[0] + texts[1] and "[shipping_cutoff" in texts[0] + texts[1]
        assert not any("brand_tone" in t for t in texts)

    # next week, while the review task is still open: no second task
    run = run_day(factory, w, DAY1 + timedelta(days=7), services, "knowledge.staleness")
    assert run.status == "no_action" and "still open" in run.summary
    # once it's closed out, the following week opens a fresh one
    with factory() as s:
        operations.set_task_status(s, w.agent("account_lead"), task.id, "cancelled", "superseded")
        s.commit()
    run = run_day(factory, w, DAY1 + timedelta(days=14), services, "knowledge.staleness")
    assert run.status == "succeeded" and len(run.produced["tasks"]) == 1


def test_staleness_nothing_stale(factory, world, session, vault):
    _approved_fact(session, world, "brand_tone", date(2027, 1, 1))
    session.commit()
    run = run_day(factory, world, DAY1, {"today": date(2026, 10, 9)}, "knowledge.staleness")
    assert run.status == "no_action" and "no approved fact is past its review date" in run.summary


# ---------------------------------------------------------------- results.due

def _pending_result(session, w):
    ins, _ = operations.create_insight(session, w.agent("analyst"), area="flow_audit", title="Welcome flow is off",
                                       evidence=[{"kind": "check", "ref": "scorecard:1#FLW-01"}])
    d, ap = operations.propose_decision(session, w.agent("strategist"), title="Rebuild welcome", insight_ids=[ins.id],
                                        rationale="no welcome", measurement={"metric": "welcome_rpr", "window_days": 30,
                                                                             "baseline": 0.4})
    approvals.decide(session, w.user(w.approver), ap.id, True)
    t = operations.create_task(session, w.agent("strategist"), decision_id=d.id, title="Rebuild the welcome flow",
                               area="flow_architecture", owner_role="creative", done_when="built", checklist=["ok"])
    operations.pass_check(session, w.agent("qa"), t.id, "c1")
    _, result = operations.complete_task(session, w.agent("creative"), t.id, [{"kind": "klaviyo_flow", "ref": "flow:X"}])
    session.commit()
    return result


def test_results_due_one_task_no_duplicate(factory, world, session):
    w = world
    result = _pending_result(session, w)
    end = result.window_end
    run = run_day(factory, w, DAY1, {"today": end - timedelta(days=1)}, "results.due")
    assert run.status == "no_action" and "no pending result has a closed window" in run.summary

    run = run_day(factory, w, DAY1 + timedelta(days=1), {"today": end}, "results.due")
    assert run.status == "succeeded", run.error
    with factory() as s:
        (task,) = s.scalars(select(m.Task).where(m.Task.job_run_id == run.id)).all()
        assert task.owner_role == "analyst" and task.area == "reporting" and f"result:{result.id}" in task.done_when
        assert "welcome_rpr" in task.title and "baseline 0.4" in task.checklist[1]["text"]

    run = run_day(factory, w, DAY1 + timedelta(days=2), {"today": end + timedelta(days=1)}, "results.due")
    assert run.status == "no_action" and "already has an open measurement task" in run.summary
    with factory() as s:
        assert len(s.scalars(select(m.Task).where(m.Task.owner_role == "analyst")).all()) == 1
        operations.record_result(s, w.agent("analyst"), result.id, observed=0.9, notes="welcome RPR up", today=end)
        s.commit()
    run = run_day(factory, w, DAY1 + timedelta(days=3), {"today": end + timedelta(days=2)}, "results.due")
    assert run.status == "no_action"


# ---------------------------------------------------------------- contracts

def test_cadence_contracts_are_registered_but_not_scheduled(factory, world, session):
    session.commit()
    for key in IMPLEMENTED:
        assert jobs.REGISTRY[key].handler is not None
    expected = {"post_send.review", "weekly.trends", "weekly.calendar_readiness", "biweekly.meeting_prep",
                "monthly.report", "monthly.calendar", "quarterly.roadmap", "event.anomaly", "event.tracking_failure",
                "event.client_feedback"}
    assert set(CONTRACT_ONLY) == expected
    for key in expected:
        d = jobs.REGISTRY[key]
        assert d.handler is None and d.checks and d.action and d.completion and d.owner_role in m.AGENT_ROLES
        assert (d.cadence == "event") == bool(d.events)
    assert jobs.REGISTRY["post_send.review"].events == ("campaign.sent",)
    assert jobs.REGISTRY["event.anomaly"].events == ("metric.anomaly",)
    with factory() as s:
        queued = {r.job_key for r in jobs.enqueue_due(s, now=DAY1, org_id=world.org.id)}
        assert queued >= set(IMPLEMENTED) and not queued & expected
        s.rollback()
