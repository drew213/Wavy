from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from wavyos import demo, tenancy
from wavyos import models as m
from wavyos.api import create_app
from wavyos.db import create_all, make_engine, make_sessionmaker
from wavyos.execution import engine
from wavyos.execution.adapters import FakeAdapter


@pytest.fixture()
def env(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'c.db'}")  # a file: the test client runs requests on other threads
    create_all(eng)
    factory = make_sessionmaker(eng)
    token = demo.build(factory)
    app = create_app(factory=factory)
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer " + token
    return factory, client


def test_console_page_and_auth(env):
    _, c = env
    page = c.get("/console")
    assert page.status_code == 200 and "Wavy OS" in page.text and page.headers["x-frame-options"] == "DENY"
    assert TestClient(c.app).get("/v1/ops/overview").status_code == 401


def test_overview_flow_and_trace(env):
    _, c = env
    ov = c.get("/v1/ops/overview").json()["clients"][0]
    assert ov["slug"] == demo.CLIENT and ov["approvals_pending"] == 1 and ov["tasks"]["done"] == 2
    flow = c.get(f"/v1/ops/clients/{demo.CLIENT}/flow").json()
    d = flow["decisions"][0]
    assert d["status"] == "approved" and len(d["tasks"]) == 3
    assert all(t["result"]["status"] == "pending" for t in d["tasks"] if t["status"] == "done")
    xs = c.get("/v1/ops/executions").json()["executions"]
    rework = next(x for x in xs if x["purpose"] == "produce" and x["attempt"] == 2)
    detail = c.get(f"/v1/ops/executions/{rework['id']}").json()
    assert "detox" in detail["packet"]["previous_qa_feedback"]
    assert {s["kind"] for s in detail["steps"]} >= {"model_call", "policy", "tool_call"} and detail["artifacts"]
    assert c.get("/v1/ops/events").json()["events"]
    assert c.get("/v1/ops/jobs").status_code == 200


def test_approve_in_console_resumes_work(env):
    factory, c = env
    ap = c.get("/v1/ops/approvals").json()["approvals"][0]
    r = c.post(f"/v1/ops/approvals/{ap['id']}/decide", json={"approve": True, "note": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    done = engine.work(factory, {"fake": FakeAdapter(lambda p: demo._qa(p) if p["purpose"] == "qa" else demo._produce(p))})
    assert [(e.purpose, e.status) for e in done] == [("produce", "succeeded"), ("qa", "succeeded")]
    with factory() as s:
        assert {t.status for t in s.query(m.Task)} == {"done"}


def test_dispatch_and_cancel(env):
    factory, c = env
    with factory() as s:
        d = s.query(m.Decision).one()
        agent = s.query(m.Agent).filter_by(role="strategist").one()
        from wavyos import operations
        from wavyos.scope import scope_for_agent
        t = operations.create_task(s, scope_for_agent(s, agent, d.client_id), decision_id=d.id, title="Welcome email 3",
                                   area="voice_copy", owner_role="creative", done_when="saved", checklist=["no health or detox claims"])
        s.commit()
        tid = t.id
    r = c.post(f"/v1/ops/clients/{demo.CLIENT}/tasks/{tid}/dispatch", json={})
    assert r.status_code == 200 and r.json()["executor"] == "fake"
    assert c.post(f"/v1/ops/clients/{demo.CLIENT}/tasks/{tid}/dispatch", json={}).status_code == 409
    ex_id = r.json()["execution_id"]
    assert c.post(f"/v1/ops/executions/{ex_id}/cancel").status_code == 200


def test_client_stakeholder_sees_only_their_client(env):
    factory, c = env
    with factory() as s:
        owner = s.query(m.User).filter_by(email="owner@northwind.demo.test").one()
        tok = tenancy.issue_token(s, owner, "t")
        admin = s.query(m.User).filter_by(email="admin@demo.wavy.test").one()
        from wavyos.scope import scope_for_user
        tenancy.create_client(s, scope_for_user(s, admin, None), name="Other brand", slug="other-demo")
        s.commit()
    oc = TestClient(c.app)
    oc.headers["Authorization"] = "Bearer " + tok
    assert [x["slug"] for x in oc.get("/v1/ops/overview").json()["clients"]] == [demo.CLIENT]
    assert oc.get("/v1/ops/clients/other-demo/flow").status_code == 404
    assert len(c.get("/v1/ops/overview").json()["clients"]) == 2
