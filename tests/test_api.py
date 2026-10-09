from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wavyos import approvals, tenancy
from wavyos import models as m
from wavyos.api import create_app
from wavyos.config import Settings
from wavyos.db import create_all, make_engine, make_sessionmaker, utcnow
from wavyos.scope import scope_for_agent, scope_for_user
from wavyos.vault import LocalKeyProvider, Vault

AUDIT_OS = Path(os.environ.get("WAVYOS_TEST_AUDIT_OS_ROOT", "var/audit-os"))
EVIDENCE = [{"kind": "check", "ref": "scorecard:1#FLW-01", "detail": "welcome flow is draft"}]


class World:
    pass


@pytest.fixture()
def api(tmp_path):
    """A file-backed SQLite DB (TestClient runs routes on worker threads), two clients, a second org."""
    engine = make_engine(f"sqlite:///{tmp_path / 'api.db'}")
    create_all(engine)
    factory = make_sessionmaker(engine)
    w = World()
    with factory() as s:
        org, admin = tenancy.bootstrap_org(s, name="Agency", slug="agency", admin_email="admin@agency.test",
                                           admin_name="Admin")
        admin_scope = scope_for_user(s, admin, None)
        a = tenancy.create_client(s, admin_scope, name="Brand A", slug="brand-a")
        b = tenancy.create_client(s, admin_scope, name="Brand B", slug="brand-b")
        lead = tenancy.add_user(s, admin_scope, email="lead@agency.test", name="Lead")
        tenancy.grant(s, admin_scope, user_id=lead.id, role="account_lead", client_id=None)
        qa = tenancy.add_user(s, admin_scope, email="qa@agency.test", name="QA")
        tenancy.grant(s, admin_scope, user_id=qa.id, role="qa", client_id=None)
        owner = tenancy.add_user(s, admin_scope, email="owner@brand-a.test", name="Owner", kind="client")
        tenancy.grant(s, admin_scope, user_id=owner.id, role="client_approver", client_id=a.id)
        org2, admin2 = tenancy.bootstrap_org(s, name="Other Agency", slug="other", admin_email="admin@other.test",
                                             admin_name="Other")
        tenancy.create_client(s, scope_for_user(s, admin2, None), name="Brand A elsewhere", slug="brand-a-two")
        w.tokens = {name: tenancy.issue_token(s, u) for name, u in
                    [("admin", admin), ("lead", lead), ("qa", qa), ("owner", owner), ("admin2", admin2)]}
        w.org_id, w.a_id, w.b_id = org.id, a.id, b.id
        s.commit()
    w.factory = factory
    w.audit_root = tmp_path / "audit-output"
    w.audit_root.mkdir()
    w.verify_calls = []
    w.audit_calls = []

    def fake_verify(api_key):
        w.verify_calls.append(api_key)
        if api_key.endswith("_bad"):
            raise RuntimeError(f"401 for key {api_key}")  # a careless connector quoting the key
        return {"id": "AbC123", "name": "Test Account", "timezone": "UTC"}

    def fake_run_audit(session, scope, snapshot_dir, track):
        w.audit_calls.append((Path(snapshot_dir), track))
        sc = m.Scorecard(org_id=scope.org_id, client_id=scope.client_id, track=track, library_version="0.1.0",
                         snapshot_ref=str(snapshot_dir.name), summary={"pass": 1}, created_by=scope.actor.ref)
        sc.results.append(m.CheckResult(check_id="FLW-01", verdict="pass", message="welcome is live"))
        session.add(sc)
        session.flush()
        return sc

    def fake_compare(session, scope, before_id, after_id):
        return {"before": before_id, "after": after_id, "changes": []}

    settings = Settings(database_url="sqlite://", master_key_file=tmp_path / "master.key",
                        audit_output_root=w.audit_root, audit_os_root=AUDIT_OS)
    app = create_app(factory, services={
        "settings": settings, "vault": Vault(LocalKeyProvider(tmp_path / "master.key")),
        "klaviyo_verify": fake_verify, "run_audit": fake_run_audit, "compare_audits": fake_compare})
    w.client = TestClient(app)

    def call(method, path, who=None, **kw):
        headers = kw.pop("headers", {})
        if who:
            headers["Authorization"] = f"Bearer {w.tokens[who]}"
        return w.client.request(method, path, headers=headers, **kw)
    w.call = call
    return w


def ok(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.json()


# ---------------------------------------------------------------- auth and tenancy

def test_auth_required(api):
    assert api.call("GET", "/healthz").json() == {"status": "ok"}
    r = api.call("GET", "/v1/me")
    assert r.status_code == 401 and "error" in r.json()
    assert api.call("GET", "/v1/me", headers={"Authorization": "Bearer wos_not-a-real-token"}).status_code == 401
    assert api.call("GET", "/v1/me", headers={"Authorization": "Basic abc"}).status_code == 401
    assert api.call("GET", "/v1/clients/brand-a/facts", headers={"Authorization": "Bearer nope"}).status_code == 401
    me = ok(api.call("GET", "/v1/me", "lead"))
    assert me["email"] == "lead@agency.test" and me["memberships"][0]["role"] == "account_lead"
    assert "token_hash" not in json.dumps(me)


def test_tenant_isolation(api):
    # client stakeholder of A sees only A, and B is a 404 (indistinguishable from absent)
    assert [c["slug"] for c in ok(api.call("GET", "/v1/clients", "owner"))] == ["brand-a"]
    assert api.call("GET", "/v1/clients/brand-b/facts", "owner").status_code == 404
    assert api.call("GET", "/v1/clients/brand-a/facts", "owner").status_code == 200
    # a user of another organization can't reach this org's clients by slug
    assert api.call("GET", "/v1/clients/brand-a/insights", "admin2").status_code == 404
    assert [c["slug"] for c in ok(api.call("GET", "/v1/clients", "admin2"))] == ["brand-a-two"]
    # an object id from client A addressed through client B is a 404
    ins = ok(api.call("POST", "/v1/clients/brand-a/insights", "lead",
                      json={"area": "flow_audit", "title": "Welcome flow is off", "evidence": EVIDENCE}), 201)
    iid = ins["insight"]["id"]
    r = api.call("POST", f"/v1/clients/brand-b/insights/{iid}/dismiss", "lead", json={"reason": "x"})
    assert r.status_code == 404 and r.json() == {"error": "insight not found"}
    assert ok(api.call("GET", "/v1/clients/brand-b/insights", "lead")) == []
    # agency-level routes need an agency role
    assert api.call("POST", "/v1/clients", "owner", json={"name": "X", "slug": "xx"}).status_code == 403


def test_errors_map_and_roll_back(api):
    r = api.call("POST", "/v1/clients/brand-a/insights", "lead", json={"area": "nowhere", "title": "t",
                                                                       "evidence": EVIDENCE})
    assert r.status_code == 422 and "unknown area" in r.json()["error"]
    r = api.call("POST", "/v1/clients/brand-a/insights", "lead", json={"area": "flow_audit", "title": "t"})
    assert r.status_code == 422 and r.json()["error"].startswith("evidence")
    ok(api.call("POST", "/v1/clients", "lead", json={"name": "Brand C", "slug": "brand-c"}), 201)
    assert api.call("POST", "/v1/clients", "lead", json={"name": "Brand C", "slug": "brand-c"}).status_code == 409
    # a request that fails leaves nothing behind: the failed decision proposal wrote no event
    before = len(ok(api.call("GET", "/v1/clients/brand-a/events?limit=500", "lead")))
    r = api.call("POST", "/v1/clients/brand-a/decisions", "lead", json={
        "title": "x", "insight_ids": ["0" * 32], "measurement": {"metric": "m", "window_days": 7}})
    assert r.status_code == 404
    assert len(ok(api.call("GET", "/v1/clients/brand-a/events?limit=500", "lead"))) == before


# ---------------------------------------------------------------- the operating chain over HTTP

def test_full_chain_over_http(api):
    c = "/v1/clients/brand-a"
    ins = ok(api.call("POST", f"{c}/insights", "lead", json={
        "area": "flow_audit", "title": "Welcome flow is off", "evidence": EVIDENCE, "severity": "critical"}), 201)
    iid = ins["insight"]["id"]
    prop = ok(api.call("POST", f"{c}/decisions", "lead", json={
        "title": "Rebuild welcome", "insight_ids": [iid], "rationale": "no welcome since March",
        "measurement": {"metric": "welcome_rpr", "window_days": 30, "baseline": 0.0},
        "plan_scope": ["flow.build_draft"]}), 201)
    did, apid = prop["decision"]["id"], prop["approval"]["id"]
    assert [a["id"] for a in ok(api.call("GET", f"{c}/approvals", "owner"))] == [apid]
    # the proposer can't approve their own request; tasks can't start before approval
    r = api.call("POST", f"{c}/approvals/{apid}/decide", "lead", json={"approve": True})
    assert r.status_code == 403
    task_body = {"decision_id": did, "title": "Draft new welcome flow", "area": "flow_architecture",
                 "owner_role": "creative", "done_when": "3 emails built as draft",
                 "checklist": ["matches approved offer", "links resolve"]}
    assert api.call("POST", f"{c}/tasks", "lead", json=task_body).status_code == 409
    decided = ok(api.call("POST", f"{c}/approvals/{apid}/decide", "owner", json={"approve": True, "note": "go"}))
    assert decided["status"] == "approved" and decided["decided_by"].startswith("user:")
    assert api.call("POST", f"{c}/approvals/{apid}/decide", "admin", json={"approve": False}).status_code == 409

    task = ok(api.call("POST", f"{c}/tasks", "lead", json=task_body), 201)
    tid = task["id"]
    ok(api.call("POST", f"{c}/tasks/{tid}/status", "lead", json={"status": "in_progress"}))
    done_body = {"outputs": [{"kind": "klaviyo_flow", "ref": "flow:XYZ", "label": "Welcome v2"}]}
    assert api.call("POST", f"{c}/tasks/{tid}/complete", "lead", json=done_body).status_code == 409
    assert api.call("POST", f"{c}/tasks/{tid}/checks/c1", "lead").status_code == 403  # author can't QA itself
    for item in ("c1", "c2"):
        ok(api.call("POST", f"{c}/tasks/{tid}/checks/{item}", "qa", json={"passed": True}))
    assert api.call("POST", f"{c}/tasks/{tid}/complete", "lead", json={"outputs": []}).status_code == 409
    finished = ok(api.call("POST", f"{c}/tasks/{tid}/complete", "lead", json=done_body))
    rid = finished["result"]["id"]
    assert finished["task"]["status"] == "done" and finished["result"]["status"] == "pending"

    assert ok(api.call("GET", f"{c}/results?due=true", "lead")) == []
    r = api.call("POST", f"{c}/results/{rid}", "lead", json={"observed": 1.2, "notes": "early"})
    assert r.status_code == 409 and "window" in r.json()["error"]
    with api.factory() as s:  # let the measurement window pass
        s.get(m.Result, rid).window_end = utcnow().date()
        s.commit()
    assert [x["id"] for x in ok(api.call("GET", f"{c}/results?due=true", "lead"))] == [rid]
    measured = ok(api.call("POST", f"{c}/results/{rid}", "lead", json={"observed": 1.2, "notes": "welcome RPR 1.20"}))
    assert measured["status"] == "measured"

    story = ok(api.call("GET", f"{c}/decisions/{did}", "owner"))
    assert story["decision"]["status"] == "closed"
    assert story["insights"][0]["evidence"] == EVIDENCE
    assert story["tasks"][0]["result"]["observed"] == 1.2
    chain = ok(api.call("GET", f"{c}/events/verify", "lead"))
    assert chain["ok"] and chain["count"] > 10
    actions = [e["action"] for e in ok(api.call("GET", f"{c}/events?limit=500", "lead"))]
    assert "decision.closed" in actions and "task.done" in actions


def test_facts_and_context_over_http(api):
    c = "/v1/clients/brand-a"
    prop = ok(api.call("POST", f"{c}/facts", "lead", json={
        "area": "offers", "component": "facts_rules", "key": "welcome_offer", "title": "Welcome offer is 10% off",
        "value": {"percent": 10}, "source_kind": "meeting", "source_ref": "kickoff call"}), 201)
    assert prop["fact"]["status"] == "proposed"
    assert ok(api.call("GET", f"{c}/facts", "lead")) == []
    assert len(ok(api.call("GET", f"{c}/facts?include_proposed=true", "lead"))) == 1
    ok(api.call("POST", f"{c}/approvals/{prop['approval']['id']}/decide", "owner", json={"approve": True}))
    ok(api.call("POST", "/v1/standards", "admin", json={"key": "sop.offers", "area": "offers", "component": "sop",
                                                         "title": "Offer rules", "body": "no stacking"}), 201)
    assert api.call("POST", "/v1/standards", "lead", json={"key": "x", "area": "offers", "component": "sop",
                                                            "title": "x"}).status_code == 403
    pack = ok(api.call("GET", f"{c}/context/offers", "lead"))
    assert [f["key"] for f in pack["client_facts"]] == ["welcome_offer"]
    assert [x["key"] for x in pack["standards"]] == ["sop.offers"]
    assert api.call("GET", f"{c}/context/nowhere", "lead").status_code == 422
    assert [x["key"] for x in ok(api.call("GET", "/v1/standards?area=offers", "lead"))] == ["sop.offers"]


# ---------------------------------------------------------------- secrets

def test_api_never_returns_secrets(api):
    c = "/v1/clients/brand-a"
    key = "pk_test_" + secrets.token_hex(16)
    bodies = []
    r = api.call("POST", f"{c}/connections", "lead", json={"provider": "klaviyo", "api_key": key, "label": "audit"})
    bodies.append(r.text)
    conn = ok(r, 201)
    assert set(conn) == {"id", "provider", "external_account_id", "status"}
    assert conn["external_account_id"] == "AbC123" and api.verify_calls == [key]
    bodies.append(api.call("GET", f"{c}/connections", "lead").text)
    # same account again: conflict, and the error doesn't echo the key
    bodies.append(api.call("POST", f"{c}/connections", "lead", json={"provider": "klaviyo", "api_key": key}).text)
    # a verifier that quotes the key in its error
    bad = key + "_bad"
    r = api.call("POST", f"{c}/connections", "lead", json={"provider": "klaviyo", "api_key": bad})
    assert r.status_code == 422
    bodies.append(r.text)
    # a malformed request: validation errors don't echo inputs
    r = api.call("POST", f"{c}/connections", "lead", json={"provider": "klaviyo", "api_key": key, "extra": key})
    assert r.status_code == 422
    bodies.append(r.text)
    # a client approver can't add connections, and the key never reaches the verifier
    calls = len(api.verify_calls)
    r = api.call("POST", f"{c}/connections", "owner", json={"provider": "klaviyo", "api_key": key})
    assert r.status_code == 403 and len(api.verify_calls) == calls
    bodies.append(r.text)
    r = api.call("DELETE", f"{c}/connections/{conn['id']}?reason=audit%20delivered", "lead")
    assert ok(r)["status"] == "revoked"
    bodies.append(r.text)
    bodies.append(api.call("GET", f"{c}/events?limit=500", "lead").text)
    bodies.append(api.call("GET", "/v1/me", "lead").text)
    for text in bodies:
        assert key not in text
        assert "secret_ciphertext" not in text and "token_hash" not in text
    with api.factory() as s:
        row = s.get(m.Connection, conn["id"])
        assert row.status == "revoked" and key.encode() not in (row.secret_ciphertext or b"")


# ---------------------------------------------------------------- audits

def test_audit_paths_are_confined(api, tmp_path):
    c = "/v1/clients/brand-a"
    root = api.audit_root
    (root / "brand-a-2026-10-01").mkdir()
    (root / "brand-a-2026-10-01" / "flows.json").write_text("{}")
    (root / "brand-b-2026-10-01").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "brand-a-escape").symlink_to(outside, target_is_directory=True)
    (root / "brand-a-sneaky").symlink_to(root / "brand-b-2026-10-01", target_is_directory=True)
    (root / "brand-a-leaky").mkdir()
    (root / "brand-a-leaky" / "flows.json").symlink_to(outside / "secret.json")
    for slug in ["../outside", "..", "/etc", str(outside), "brand-a-2026-10-01/../../outside", "brand-a/x",
                 "brand-b-2026-10-01", "brand-a-escape", "brand-a-sneaky", "brand-a-leaky", "", ".hidden"]:
        r = api.call("POST", f"{c}/audits", "lead", json={"snapshot_slug": slug, "track": "snapshot"})
        assert r.status_code == 422, (slug, r.text)
    assert api.call("POST", f"{c}/audits", "lead", json={"snapshot_slug": "brand-a-1999-01-01"}).status_code == 404
    assert api.audit_calls == []
    # a client approver may not run analysis
    assert api.call("POST", f"{c}/audits", "owner", json={"snapshot_slug": "brand-a-2026-10-01"}).status_code == 403

    sc = ok(api.call("POST", f"{c}/audits", "lead", json={"snapshot_slug": "brand-a-2026-10-01", "track": "deep"}), 201)
    assert api.audit_calls == [((root / "brand-a-2026-10-01").resolve(), "deep")]
    assert sc["results"][0]["check_id"] == "FLW-01"
    got = ok(api.call("GET", f"{c}/scorecards/{sc['id']}", "owner"))
    assert got["id"] == sc["id"]
    sc2 = ok(api.call("POST", f"{c}/audits", "lead", json={"snapshot_slug": "brand-a-2026-10-01"}), 201)
    cmp_ = ok(api.call("GET", f"{c}/scorecards/{sc['id']}/compare/{sc2['id']}", "lead"))
    assert cmp_["before"] == sc["id"]
    assert api.call("GET", f"/v1/clients/brand-b/scorecards/{sc['id']}", "lead").status_code == 404


# ---------------------------------------------------------------- policy

def test_policy_catalog_overrides_and_dry_run(api):
    c = "/v1/clients/brand-a"
    catalog = {a["key"]: a for a in ok(api.call("GET", "/v1/policy/actions", "owner"))}
    assert catalog["profile.suppress_delete"]["locked"] and catalog["data.read"]["level"] == "autonomous"
    r = api.call("POST", "/v1/policy/overrides", "admin", json={"action": "profile.suppress_delete",
                                                                "level": "autonomous", "reason": "speed"})
    assert r.status_code == 403
    assert api.call("POST", "/v1/policy/overrides", "lead", json={"action": "campaign.publish", "level": "approved_plan",
                                                                   "reason": "x"}).status_code == 403
    assert api.call("POST", "/v1/policy/overrides", "admin", json={
        "action": "made.up", "level": "autonomous", "reason": "x"}).status_code == 422
    o = ok(api.call("POST", "/v1/policy/overrides", "admin", json={
        "action": "campaign.publish", "level": "approved_plan", "reason": "10 clean sends",
        "client_slug": "brand-b"}), 201)
    assert o["client_id"] == api.b_id
    assert api.call("POST", "/v1/policy/overrides", "admin", json={
        "action": "campaign.publish", "level": "approved_plan", "reason": "x",
        "client_slug": "brand-a-two"}).status_code == 404  # another org's client

    # an approval for one exact payload, approved by the client
    payload = {"campaign": "bfcm-1", "version": 3}
    with api.factory() as s:
        agent = s.query(m.Agent).filter_by(org_id=api.org_id, role="account_lead").one()
        ap = approvals.request(s, scope_for_agent(s, agent, api.a_id), "campaign.publish", "campaign", "bfcm-1",
                               "Send BFCM 1", payload)
        s.commit()
        apid = ap.id
    body = {"action": "campaign.publish", "approval_id": apid, "payload": payload}
    assert ok(api.call("POST", f"{c}/authorize", "lead", json=body))["outcome"] == "needs_approval"
    ok(api.call("POST", f"{c}/approvals/{apid}/decide", "owner", json={"approve": True}))
    for _ in range(3):  # a dry run never consumes it
        res = ok(api.call("POST", f"{c}/authorize", "lead", json=body))
        assert res["outcome"] == "allow" and res["dry_run"]
    changed = ok(api.call("POST", f"{c}/authorize", "lead", json={**body, "payload": {**payload, "version": 4}}))
    assert changed["outcome"] == "needs_approval" and "changed" in changed["reason"]
    assert ok(api.call("POST", f"{c}/authorize", "lead", json={"action": "flow.build_draft"}))["outcome"] == "needs_plan"
    assert ok(api.call("POST", f"{c}/authorize", "owner", json={"action": "task.manage"}))["outcome"] == "deny"
    with api.factory() as s:
        assert s.get(m.Approval, apid).used_at is None
        assert s.query(m.Event).filter(m.Event.action.like("policy.%"), m.Event.object_id == apid).count() == 0


# ---------------------------------------------------------------- jobs

def test_job_definitions_and_runs(api):
    defs = ok(api.call("GET", "/v1/jobs/definitions", "lead"))
    assert isinstance(defs, list) and all({"key", "cadence", "owner_role", "implemented"} <= set(d) for d in defs)
    assert ok(api.call("GET", "/v1/clients/brand-a/job-runs", "lead")) == []


# ---------------------------------------------------------------- seed

@pytest.mark.skipif(not (AUDIT_OS / "sop").is_dir(), reason="audit-os isn't on this machine")
def test_seed_imports_standards_idempotently(api):
    from wavyos.seed import LIBRARY_KEY, import_audit_os_standards
    with api.factory() as s:
        admin = s.query(m.User).filter_by(email="admin@agency.test").one()
        scope = scope_for_user(s, admin, None)
        first = import_audit_os_standards(s, scope, AUDIT_OS)
        s.commit()
        second = import_audit_os_standards(s, scope, AUDIT_OS)
        s.commit()
        n_sops = len(list((AUDIT_OS / "sop").glob("*.md")))
        assert len(first) == n_sops + 1 and all(x.changed for x in first)
        assert not any(x.changed for x in second)
        rows = s.query(m.Standard).filter_by(org_id=api.org_id).all()
        assert len(rows) == n_sops + 1 and all(r.version == 1 for r in rows)
        by_key = {r.key: r for r in rows}
        assert by_key["sop.audit.intake"].area == "onboarding"
        assert by_key["sop.audit.access"].area == "data_infrastructure"
        assert by_key["sop.audit.reaudit"].area == "reporting"
        assert by_key["sop.audit.intake"].data["checklist"][0]["id"] == "intake-created"
        lib = by_key[LIBRARY_KEY]
        assert lib.component == "quality_checklist" and len(lib.data["checks"]) > 0
    keys = {x["key"] for x in ok(api.call("GET", "/v1/standards", "lead"))}
    assert "sop.audit.checks" in keys and LIBRARY_KEY in keys
    # the stage-to-area mapping only uses real operating areas
    from wavyos.seed import STAGE_AREAS
    from wavyos.taxonomy import AREAS
    assert set(STAGE_AREAS.values()) <= set(AREAS)
