from __future__ import annotations

from datetime import date, timedelta

import pytest

from wavyos import approvals, connections, events, jobs, knowledge, operations, policy, tenancy
from wavyos import models as m
from wavyos.db import utcnow
from wavyos.scope import InvalidState, NotFound, NotPermitted
from wavyos.vault import VaultError

EVIDENCE = [{"kind": "check", "ref": "scorecard:1#FLW-01", "detail": "welcome flow is draft"}]


def _insight(session, scope, **kw):
    ins, _ = operations.create_insight(session, scope, area="flow_audit", title="Welcome flow is off",
                                       evidence=EVIDENCE, severity="critical", **kw)
    return ins


def _approved_decision(session, w, plan_scope=("flow.build_draft",)):
    analyst = w.agent("analyst")
    ins = _insight(session, analyst)
    d, ap = operations.propose_decision(session, w.agent("strategist"), title="Rebuild welcome", insight_ids=[ins.id],
                                        rationale="no welcome since March", plan_scope=list(plan_scope),
                                        measurement={"metric": "welcome_rpr", "window_days": 30, "baseline": 0.0})
    approvals.decide(session, w.user(w.approver), ap.id, True, "go")
    return d, ins


# ---------------------------------------------------------------- tenancy and isolation

def test_client_isolation(session, world):
    w = world
    ins = _insight(session, w.agent("analyst", w.a))
    with pytest.raises(NotFound):
        operations.dismiss_insight(session, w.agent("analyst", w.b), ins.id, "wrong client")
    # client stakeholder of A has no access to B at all
    with pytest.raises(NotFound):
        w.user(w.approver, w.b)


def test_tokens_hash_only(session, world):
    token = tenancy.issue_token(session, world.lead, "cli")
    assert session.query(m.ApiToken).filter(m.ApiToken.token_hash == token).count() == 0
    assert tenancy.user_for_token(session, token).id == world.lead.id
    assert tenancy.user_for_token(session, token + "x") is None
    tenancy.revoke_token(session, world.lead, token[:12])
    assert tenancy.user_for_token(session, token) is None


# ---------------------------------------------------------------- policy

def test_autonomous_and_denied(session, world):
    w = world
    assert policy.authorize(session, w.agent("analyst"), "analysis.run").allowed
    viewer_scope = w.user(w.approver)
    assert policy.authorize(session, viewer_scope, "task.manage").outcome == "deny"
    assert policy.authorize(session, w.agent("analyst"), "connection.manage").outcome == "deny"  # human only
    assert policy.authorize(session, w.agent("analyst"), "made.up").outcome == "deny"


def test_approved_plan_requires_covering_decision(session, world):
    w = world
    creative = w.agent("creative")
    assert policy.authorize(session, creative, "flow.build_draft").outcome == "needs_plan"
    d, _ = _approved_decision(session, w)
    assert policy.authorize(session, creative, "flow.build_draft", decision_id=d.id).allowed
    assert policy.authorize(session, creative, "campaign.build_draft", decision_id=d.id).outcome == "needs_plan"
    # a decision from another client doesn't count
    assert policy.authorize(session, w.agent("creative", w.b), "flow.build_draft", decision_id=d.id).outcome == "needs_plan"


def test_explicit_approval_is_bound_single_use(session, world):
    w = world
    lead = w.user(w.lead)
    payload = {"campaign": "bfcm-1", "version": 3}
    ap = approvals.request(session, w.agent("account_lead"), "campaign.publish", "campaign", "bfcm-1", "Send BFCM 1", payload)
    assert policy.authorize(session, lead, "campaign.publish", approval_id=ap.id, payload=payload).outcome == "needs_approval"
    approvals.decide(session, w.user(w.approver), ap.id, True)
    changed = {"campaign": "bfcm-1", "version": 4}
    assert policy.authorize(session, lead, "campaign.publish", approval_id=ap.id, payload=changed).outcome == "needs_approval"
    assert policy.authorize(session, lead, "campaign.publish", approval_id=ap.id, payload=payload).allowed
    assert policy.authorize(session, lead, "campaign.publish", approval_id=ap.id, payload=payload).outcome == "needs_approval"


def test_approval_rules(session, world):
    w = world
    ap = approvals.request(session, w.user(w.lead), "client.message.send", "message", "m1", "Weekly update", {"body": "hi"})
    with pytest.raises(NotPermitted):
        approvals.decide(session, w.user(w.lead), ap.id, True)        # own request
    with pytest.raises(NotPermitted):
        approvals.decide(session, w.user(w.approver), ap.id, True)    # client can't approve agency messages
    with pytest.raises(NotPermitted):
        approvals.decide(session, w.agent("qa"), ap.id, True)         # agents never approve
    approvals.decide(session, w.user(w.admin), ap.id, True)
    with pytest.raises(InvalidState):
        approvals.decide(session, w.user(w.admin), ap.id, False)


def test_locked_actions_cannot_relax(session, world):
    w = world
    admin = w.user(w.admin)
    with pytest.raises(NotPermitted):
        policy.set_override(session, admin, "profile.suppress_delete", "autonomous", "speed", client_id=w.a.id)
    policy.set_override(session, admin, "campaign.publish", "approved_plan", "10 clean sends", client_id=w.a.id)
    lvl, _ = policy.effective_level(session, w.agent("account_lead"), policy.ACTIONS["campaign.publish"])
    assert lvl == "approved_plan"
    lvl_b, _ = policy.effective_level(session, w.agent("account_lead", w.b), policy.ACTIONS["campaign.publish"])
    assert lvl_b == "explicit_approval"
    with pytest.raises(NotPermitted):
        policy.set_override(session, w.user(w.lead), "campaign.publish", "autonomous", "x", client_id=w.a.id)


# ---------------------------------------------------------------- knowledge

def test_facts_need_approval_and_agents_see_only_approved(session, world):
    w = world
    fact, ap = knowledge.propose_fact(session, w.agent("account_lead"), area="offers", component="facts_rules",
                                      key="welcome_offer", title="Welcome offer is 10% off", value={"percent": 10},
                                      source_kind="meeting", source_ref="kickoff 2026-10-09",
                                      review_by=date.today() + timedelta(days=90))
    assert knowledge.facts(session, w.agent("creative")) == []
    with pytest.raises(NotPermitted):
        knowledge.facts(session, w.agent("creative"), include_proposed=True)
    approvals.decide(session, w.user(w.approver), ap.id, True)
    assert [f.key for f in knowledge.facts(session, w.agent("creative"))] == ["welcome_offer"]
    # a newer fact supersedes the old one once approved
    f2, ap2 = knowledge.propose_fact(session, w.agent("account_lead"), area="offers", component="facts_rules",
                                     key="welcome_offer", title="Welcome offer is 15% off", value={"percent": 15},
                                     source_kind="document", source_ref="offer sheet v2")
    approvals.decide(session, w.user(w.lead), ap2.id, True)
    current = knowledge.facts(session, w.agent("creative"))
    assert [f.value["percent"] for f in current] == [15]
    assert session.get(m.ClientFact, fact.id).status == "retired"
    assert [f.id for f in knowledge.stale_facts(session, w.agent("account_lead"))] == [f2.id]  # no review date


def test_facts_dont_cross_clients(session, world):
    w = world
    _, ap = knowledge.propose_fact(session, w.agent("account_lead"), area="voice_copy", component="facts_rules",
                                   key="tone", title="Playful", source_kind="intake", source_ref="intake form")
    approvals.decide(session, w.user(w.lead), ap.id, True)
    assert knowledge.facts(session, w.agent("creative", w.b)) == []


def test_standards_versioning(session, world):
    org_scope = world.user(world.admin, None)
    s1 = knowledge.publish_standard(session, org_scope, key="sop.flow_audit", area="flow_audit", component="sop",
                                    title="Flow audit", body="v1")
    same = knowledge.publish_standard(session, org_scope, key="sop.flow_audit", area="flow_audit", component="sop",
                                      title="Flow audit", body="v1")
    assert same.id == s1.id
    s2 = knowledge.publish_standard(session, org_scope, key="sop.flow_audit", area="flow_audit", component="sop",
                                    title="Flow audit", body="v2")
    assert (s2.version, session.get(m.Standard, s1.id).status) == (2, "superseded")
    with pytest.raises(NotPermitted):
        knowledge.publish_standard(session, world.user(world.lead, None), key="x", area="flow_audit", component="sop", title="x")


# ---------------------------------------------------------------- operating chain

def test_full_chain_insight_to_result(session, world):
    w = world
    d, ins = _approved_decision(session, w)
    assert session.get(m.Insight, ins.id).status == "actioned"
    t = operations.create_task(session, w.agent("strategist"), decision_id=d.id, title="Draft new welcome flow",
                               area="flow_architecture", owner_role="creative", done_when="3 emails built as draft",
                               checklist=["matches approved offer", "links resolve"])
    with pytest.raises(InvalidState):
        operations.complete_task(session, w.agent("creative"), t.id, [{"kind": "klaviyo_flow", "ref": "flow:XYZ"}])
    with pytest.raises(NotPermitted):  # the author can't pass its own QA
        operations.pass_check(session, w.agent("strategist"), t.id, "c1")
    for item in ("c1", "c2"):
        operations.pass_check(session, w.agent("qa"), t.id, item)
    with pytest.raises(InvalidState):
        operations.complete_task(session, w.agent("creative"), t.id, [])
    t, result = operations.complete_task(session, w.agent("creative"), t.id, [{"kind": "klaviyo_flow", "ref": "flow:XYZ"}])
    assert result.status == "pending" and result.window_end == utcnow().date() + timedelta(days=30)
    with pytest.raises(InvalidState):
        operations.record_result(session, w.agent("analyst"), result.id, observed=1.2, notes="early")
    operations.record_result(session, w.agent("analyst"), result.id, observed=1.2, notes="welcome RPR $1.20",
                             today=result.window_end)
    assert session.get(m.Decision, d.id).status == "closed"
    story = operations.chain(session, w.agent("analyst"), d.id)
    assert story["tasks"][0]["result"]["observed"] == 1.2 and story["insights"][0]["evidence"] == EVIDENCE


def test_chain_guards(session, world):
    w = world
    with pytest.raises(ValueError):
        operations.create_insight(session, w.agent("analyst"), area="flow_audit", title="no evidence", evidence=[])
    ins = _insight(session, w.agent("analyst"), dedupe_key="FLW-01")
    again, created = operations.create_insight(session, w.agent("analyst"), area="flow_audit", title="dup",
                                               evidence=EVIDENCE, dedupe_key="FLW-01")
    assert again.id == ins.id and not created
    with pytest.raises(ValueError):
        operations.propose_decision(session, w.agent("strategist"), title="x", insight_ids=[ins.id], rationale="",
                                    measurement={})
    with pytest.raises(ValueError):  # plans can't authorize explicit-approval actions
        operations.propose_decision(session, w.agent("strategist"), title="x", insight_ids=[ins.id], rationale="",
                                    measurement={"metric": "m", "window_days": 7}, plan_scope=["campaign.publish"])
    d, _ = operations.propose_decision(session, w.agent("strategist"), title="x", insight_ids=[ins.id], rationale="",
                                       measurement={"metric": "m", "window_days": 7})
    with pytest.raises(InvalidState):  # not approved yet
        operations.create_task(session, w.agent("strategist"), decision_id=d.id, title="t", area="flow_audit",
                               owner_role="creative", done_when="x", checklist=["y"])


# ---------------------------------------------------------------- event log

def test_event_chain_detects_tampering(session, world):
    w = world
    _approved_decision(session, w)
    session.flush()
    ok, _ = events.verify_chain(session, w.org.id, w.a.id)
    assert ok and events.count(session, w.org.id, w.a.id) > 5
    ev = session.query(m.Event).filter_by(chain=w.a.id, seq=3).one()
    ev.summary = "edited"
    session.flush()
    assert events.verify_chain(session, w.org.id, w.a.id) == (False, 3)


# ---------------------------------------------------------------- vault and connections

def test_vault_roundtrip_and_binding(session, world, vault):
    w = world
    lead = w.user(w.lead)
    c = connections.add(session, lead, vault, provider="klaviyo", external_account_id="AbC123", secret="pk_test_secret")
    assert b"pk_test_secret" not in c.secret_ciphertext
    with connections.secret(session, w.agent("analyst"), vault, c.id, "daily health") as s:
        assert s == "pk_test_secret"
    # the same ciphertext pasted onto another client's row won't open
    other = connections.add(session, w.user(w.lead, w.b), vault, provider="klaviyo", external_account_id="Zzz999", secret="pk_other")
    other.secret_ciphertext = c.secret_ciphertext
    with pytest.raises(VaultError):
        with connections.secret(session, w.agent("analyst", w.b), vault, other.id, "x"):
            pass
    with pytest.raises(NotPermitted):
        connections.add(session, w.agent("analyst"), vault, provider="klaviyo", external_account_id="Q", secret="pk_x")
    connections.revoke(session, lead, c.id, "audit delivered")
    with pytest.raises(InvalidState):
        with connections.secret(session, w.agent("analyst"), vault, c.id, "x"):
            pass


# ---------------------------------------------------------------- jobs

def test_scheduler_runs_once_and_requires_output_or_reason(factory, world, session):
    session.commit()
    w = world
    calls = []

    @jobs.job("test.noop", "Noop", "daily", "analyst", checks="nothing", action="nothing", completion="ran")
    def _noop(ctx):
        calls.append(ctx.scope.actor.roles)
        return jobs.JobOutcome("looked", no_action_reason="nothing due")

    @jobs.job("test.silent", "Silent", "daily", "analyst", checks="x", action="x", completion="x")
    def _silent(ctx):
        return jobs.JobOutcome("looked")

    try:
        with factory() as s:
            first = jobs.enqueue_due(s, org_id=w.org.id)
            second = jobs.enqueue_due(s, org_id=w.org.id)
            s.commit()
        assert len([r for r in first if r.job_key == "test.noop"]) == 2 and not [r for r in second if r.job_key.startswith("test.")]
        done = {r.run_key: r for r in jobs.run_pending(factory)}
        statuses = {r.job_key: set() for r in done.values()}
        for r in done.values():
            statuses[r.job_key].add(r.status)
        assert statuses["test.noop"] == {"no_action"}
        assert statuses["test.silent"] == {"failed"}
        assert calls and calls[0] == frozenset({"analyst"})
    finally:
        jobs.REGISTRY.pop("test.noop", None)
        jobs.REGISTRY.pop("test.silent", None)


def test_period_keys():
    from datetime import datetime, timezone
    t = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)  # still Oct 8 in Los Angeles
    assert jobs.period_key("daily", t, "America/Los_Angeles") == "2026-10-08"
    assert jobs.period_key("quarterly", t, "UTC") == "2026-Q4"
