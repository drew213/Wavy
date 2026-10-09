from __future__ import annotations

import pytest

from wavyos import approvals, operations
from wavyos import models as m
from wavyos.execution import engine
from wavyos.execution.adapters import CodexAdapter, ExecutorUnavailable, FakeAdapter
from wavyos.scope import InvalidState, NotPermitted

EVIDENCE = [{"kind": "check", "ref": "scorecard:1#FLW-01", "detail": "welcome flow is draft"}]


def _setup(session, w, plan_scope=("flow.build_draft",), providers=("anthropic", "codex")):
    w.a.settings = {"allowed_providers": list(providers), "daily_budget_usd": 5}
    ins, _ = operations.create_insight(session, w.agent("analyst"), area="flow_audit", title="No welcome flow",
                                       evidence=EVIDENCE, severity="critical")
    d, ap = operations.propose_decision(session, w.agent("strategist"), title="Rebuild welcome", insight_ids=[ins.id],
                                        rationale="no welcome since March", plan_scope=list(plan_scope),
                                        measurement={"metric": "welcome_rpr", "window_days": 30})
    approvals.decide(session, w.user(w.approver), ap.id, True)
    t = operations.create_task(session, w.agent("strategist"), decision_id=d.id, title="Write welcome email 1",
                               area="voice_copy", owner_role="creative", done_when="email 1 copy saved",
                               checklist=["uses the approved offer", "no health claims"])
    session.commit()
    return t


PRODUCE = [("get_context", {"area": "voice_copy"}),
           ("save_output", {"kind": "copy", "label": "Welcome email 1", "content": "Subject: Welcome in..."}),
           ("finish", {"summary": "wrote email 1"})]


def qa(passes: dict):
    def script(packet):
        calls = [("record_check", {"item_id": i["id"], "passed": passes.get(i["id"], True), "note": "checked"})
                 for i in packet["task"]["checklist"]]
        return calls + [("finish", {"summary": "reviewed"})]
    return script


def test_needs_an_allowed_provider(session, world):
    t = _setup(session, world, providers=())
    with pytest.raises(NotPermitted):
        engine.dispatch(session, world.agent("strategist"), t.id)


def test_produce_then_cross_model_qa_then_done(factory, session, world):
    w = world
    t = _setup(session, w)
    ex = engine.dispatch(session, w.agent("strategist"), t.id)
    session.commit()
    assert (ex.executor, ex.model) == ("anthropic", "claude-opus-5-5")
    assert ex.packet["knowledge"]["area"] == "voice_copy" and ex.packet["decision"]["insights"][0]["evidence"] == EVIDENCE
    adapters = {"anthropic": FakeAdapter(PRODUCE), "codex": FakeAdapter(qa({}), name="codex")}
    done = engine.work(factory, adapters)
    assert [(e.purpose, e.executor, e.status) for e in done] == [("produce", "anthropic", "succeeded"), ("qa", "codex", "succeeded")]
    with factory() as s:
        task = s.get(m.Task, t.id)
        assert task.status == "done" and task.outputs[0]["ref"].startswith("artifact:")
        assert s.query(m.Result).filter_by(task_id=t.id).one().status == "pending"
        steps = s.query(m.ExecutionStep).filter_by(execution_id=done[0].id).order_by(m.ExecutionStep.seq).all()
        kinds = [(st.kind, st.name) for st in steps]
        assert kinds[0] == ("model_call", "fake")
        assert ("policy", "draft.create") in kinds and ("tool_call", "save_output") in kinds
        qa_ex = s.get(m.Execution, done[1].id)
        assert qa_ex.packet["work_to_review"][0]["label"] == "Welcome email 1"
        assert qa_ex.agent_role == "qa"


def test_qa_failure_sends_back_with_notes(factory, session, world):
    w = world
    t = _setup(session, w)
    engine.dispatch(session, w.agent("strategist"), t.id)
    session.commit()
    rounds = iter([{"c2": False}, {}])
    adapters = {"anthropic": FakeAdapter(PRODUCE), "codex": FakeAdapter(lambda p: qa(next(rounds))(p), name="codex")}
    done = engine.work(factory, adapters)
    assert [e.purpose for e in done] == ["produce", "qa", "produce", "qa"]
    assert "no health claims" in done[2].packet["previous_qa_feedback"]
    with factory() as s:
        assert s.get(m.Task, t.id).status == "done"


def test_qa_gives_up_after_max_attempts(factory, session, world):
    w = world
    t = _setup(session, w)
    engine.dispatch(session, w.agent("strategist"), t.id)
    session.commit()
    adapters = {"anthropic": FakeAdapter(PRODUCE), "codex": FakeAdapter(qa({"c1": False}), name="codex")}
    done = engine.work(factory, adapters)
    assert len([e for e in done if e.purpose == "produce"]) == engine.MAX_ATTEMPTS
    with factory() as s:
        assert s.get(m.Task, t.id).status == "blocked"


def test_policy_denial_is_traced_and_returned_to_the_model(factory, session, world):
    w = world
    t = _setup(session, w, plan_scope=())  # the plan doesn't cover building drafts
    engine.dispatch(session, w.agent("strategist"), t.id)
    session.commit()
    script = [("build_draft", {"kind": "campaign", "spec": "x"}), *PRODUCE]
    done = engine.work(factory, {"anthropic": FakeAdapter(script), "codex": FakeAdapter(qa({}), name="codex")})
    with factory() as s:
        denied = s.query(m.ExecutionStep).filter_by(execution_id=done[0].id, kind="policy", status="denied").one()
        assert denied.name == "campaign.build_draft" and denied.output["outcome"] == "needs_plan"


def test_approval_pauses_and_resumes(factory, session, world):
    w = world
    t = _setup(session, w)
    engine.dispatch(session, w.agent("strategist"), t.id)
    session.commit()
    ask = [("request_approval", {"action": "campaign.publish", "summary": "Send welcome test", "payload": "email 1 v1"}),
           ("finish", {"summary": "asked to send"})]
    done = engine.work(factory, {"anthropic": FakeAdapter(ask), "codex": FakeAdapter(qa({}), name="codex")})
    assert done[0].status == "waiting_approval"
    with factory() as s:
        assert s.get(m.Task, t.id).status == "blocked"
        ap_id = s.get(m.Execution, done[0].id).waiting_on
        from wavyos.scope import scope_for_user
        approvals.decide(s, scope_for_user(s, s.get(m.User, w.approver.id), w.a.id), ap_id, True)
        s.commit()
        nxt = s.query(m.Execution).filter_by(task_id=t.id, status="queued").one()
        assert nxt.packet["approval_granted"]["id"] == ap_id


def test_unavailable_executor_fails_cleanly_with_trace(factory, session, world):
    w = world
    t = _setup(session, w, providers=("codex",))
    engine.dispatch(session, w.agent("strategist"), t.id)
    session.commit()

    def limited(prompt):
        raise ExecutorUnavailable("Codex usage limit reached")
    done = engine.work(factory, {"codex": CodexAdapter(runner=limited)})
    assert done[0].status == "failed" and "usage limit" in done[0].error
    with factory() as s:
        assert s.get(m.Task, t.id).status == "in_progress"  # nothing was lost; it can be re-dispatched


def test_codex_plan_runs_through_the_gateway(factory, session, world):
    w = world
    t = _setup(session, w, providers=("codex", "anthropic"))
    engine.dispatch(session, w.agent("strategist"), t.id, executor="codex")
    session.commit()
    plan = '{"tool_calls":[{"name":"save_output","input":{"kind":"copy","label":"E1","content":"Hi"}},' \
           '{"name":"finish","input":{"summary":"done"}}],"summary":"done"}'
    done = engine.work(factory, {"codex": CodexAdapter(runner=lambda p: "Here you go:\n" + plan),
                                 "anthropic": FakeAdapter(qa({}))})
    assert [(e.purpose, e.executor, e.status) for e in done] == [("produce", "codex", "succeeded"), ("qa", "anthropic", "succeeded")]


def test_budget_and_one_execution_at_a_time(session, world):
    w = world
    t = _setup(session, w)
    engine.dispatch(session, w.agent("strategist"), t.id)
    with pytest.raises(InvalidState):
        engine.dispatch(session, w.agent("strategist"), t.id)
    t2 = operations.create_task(session, w.agent("strategist"), decision_id=t.decision_id, title="Email 2", area="voice_copy",
                                owner_role="creative", done_when="x", checklist=["y"])
    session.add(m.Execution(org_id=w.org.id, client_id=w.a.id, task_id=t2.id, executor="anthropic", agent_role="creative",
                            status="succeeded", packet={}, packet_hash="x", requested_by="test", cost_usd=6.0))
    session.flush()
    with pytest.raises(InvalidState):
        engine.dispatch(session, w.agent("strategist"), t2.id)


def test_anthropic_adapter_loop_with_stub_client(factory, session, world):
    """Drives AnthropicAdapter's real tool loop against a stub with the SDK's response shape."""
    from types import SimpleNamespace as NS
    from wavyos.execution.adapters import AnthropicAdapter
    w = world
    t = _setup(session, w)
    engine.dispatch(session, w.agent("strategist"), t.id)
    session.commit()
    sent = []
    turns = iter([
        NS(model="claude-opus-5-5", stop_reason="tool_use", stop_details=None, usage=NS(input_tokens=1000, output_tokens=500),
           content=[NS(type="text", text="Saving the copy."),
                    NS(type="tool_use", id="tu1", name="save_output", input={"kind": "copy", "label": "E1", "content": "Hi"}),
                    NS(type="tool_use", id="tu2", name="finish", input={"summary": "done"})]),
    ])

    class Stub:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    sent.append(kw)
                    return next(turns)
    done = engine.work(factory, {"anthropic": AnthropicAdapter(client=Stub()), "codex": FakeAdapter(qa({}), name="codex")})
    assert done[0].status == "succeeded" and done[0].cost_usd == pytest.approx(0.014)
    req = sent[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert req["betas"] == ["server-side-fallback-2026-07-01"] and req["output_config"] == {"effort": "high"}
    assert all(tool["strict"] and tool["input_schema"]["additionalProperties"] is False for tool in req["tools"])
