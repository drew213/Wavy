"""Task-level execution: dispatch work to Claude, ChatGPT or a person, review it with a different
model, and close the loop into the operating chain.

  dispatch(task)  ->  Execution(produce) queued
  worker runs it  ->  outputs saved as artifacts, task -> review
                  ->  Execution(qa) queued, routed to a different executor where the client allows one
  QA passes all   ->  operations.complete_task -> Result opens (measured later by the analyst)
  QA fails        ->  rework with the QA notes, up to MAX_ATTEMPTS, then the task is blocked for a person
  needs approval  ->  Execution waits; a person decides; approved work resumes in a new execution

Every step is in the execution trace, and every state change is in the event log.
"""
from __future__ import annotations

import traceback
from datetime import datetime, time as dtime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from .. import approvals, events, operations, spend
from .. import models as m
from ..db import utcnow
from ..policy import authorize
from ..scope import InvalidState, NotPermitted, Scope, get_scoped, scope_for_agent
from . import packet as packets
from .adapters import Adapter, AdapterResult, ExecutorUnavailable
from .tools import Gateway, tools_for

MAX_ATTEMPTS = 3
RELEASE_BLOCKING = ("critical", "high")  # QA-flagged insight severities that block release
DEFAULT_DAILY_BUDGET_USD = 10.0

# (purpose, owner_role) -> (executor, model, effort). "*" matches any role.
ROUTES: dict[tuple[str, str], tuple[str, str, str]] = {
    ("produce", "creative"): ("anthropic", "claude-opus-5-5", "high"),
    ("produce", "strategist"): ("anthropic", "claude-opus-5-5", "high"),
    ("produce", "analyst"): ("anthropic", "claude-opus-5-5", "high"),
    ("produce", "account_lead"): ("anthropic", "claude-opus-5-5", "medium"),
    ("produce", "*"): ("anthropic", "claude-opus-5-5", "high"),
    ("qa", "*"): ("codex", "", "high"),
}
FALLBACK_MODEL = {"anthropic": "claude-opus-5-5", "codex": "", "fake": "fake", "human": ""}


def allowed_providers(client: m.Client) -> list[str]:
    return list((client.settings or {}).get("allowed_providers", []))


def route(session: Session, task: m.Task, purpose: str, *, prefer: Optional[str] = None) -> tuple[str, str, str]:
    """Pick (executor, model, effort). QA goes to a different executor than the one that produced the work."""
    client = session.get(m.Client, task.client_id)
    allowed = allowed_providers(client)
    executor, model, effort = ROUTES.get((purpose, task.owner_role)) or ROUTES[(purpose, "*")]
    if prefer:
        executor, model = prefer, FALLBACK_MODEL.get(prefer, "")
    if purpose == "qa":
        last = session.scalar(select(m.Execution).where(m.Execution.task_id == task.id, m.Execution.purpose == "produce",
                                                        m.Execution.status == "succeeded").order_by(m.Execution.created_at.desc()))
        producer = last.executor if last else None
        others = [p for p in allowed if p != producer and p != "human"]
        if executor not in allowed or executor == producer:
            if others:
                executor = others[0]
                model = FALLBACK_MODEL.get(executor, "")
            elif producer in allowed:
                executor, model = producer, (last.model if last else FALLBACK_MODEL.get(producer, ""))
    if executor not in allowed and executor != "human":
        if allowed:
            executor, model = allowed[0], FALLBACK_MODEL.get(allowed[0], "")
        else:
            raise NotPermitted("this client hasn't allowed any model provider yet (client settings: allowed_providers)")
    return executor, model, effort


def spent_today(session: Session, client_id: str) -> float:
    start = datetime.combine(utcnow().date(), dtime.min, tzinfo=timezone.utc)
    return float(session.scalar(select(func.coalesce(func.sum(m.Execution.cost_usd), 0.0)).where(
        m.Execution.client_id == client_id, m.Execution.created_at >= start)) or 0.0)


def spent_today_by_client(session: Session, client_ids) -> dict[str, float]:
    """spent_today for many clients in one query; client_ids is a list or a subquery."""
    start = datetime.combine(utcnow().date(), dtime.min, tzinfo=timezone.utc)
    return {cid: float(total or 0.0) for cid, total in session.execute(
        select(m.Execution.client_id, func.sum(m.Execution.cost_usd))
        .where(m.Execution.client_id.in_(client_ids), m.Execution.created_at >= start)
        .group_by(m.Execution.client_id))}


def _agent_scope(session: Session, scope: Scope, role: str) -> Scope:
    agent = session.scalar(select(m.Agent).where(m.Agent.org_id == scope.org_id, m.Agent.role == role, m.Agent.active.is_(True)))
    if agent is None:
        raise NotPermitted(f"no active {role} agent in this organization")
    return scope_for_agent(session, agent, scope.client_id)


def dispatch(session: Session, scope: Scope, task_id: str, *, purpose: str = "produce", executor: Optional[str] = None,
             feedback: Optional[str] = None, approval: Optional[m.Approval] = None) -> m.Execution:
    auth = authorize(session, scope, "task.manage")
    if not auth.allowed:
        raise NotPermitted(auth.reason)
    task = get_scoped(session, scope, m.Task, task_id)
    if task.status in ("done", "cancelled"):
        raise InvalidState(f"task is {task.status}")
    active = session.scalar(select(m.Execution.id).where(m.Execution.task_id == task.id,
                                                         m.Execution.status.in_(("queued", "running"))))
    if active:
        raise InvalidState("this task already has an execution in progress")
    client = session.get(m.Client, task.client_id)
    budget = float((client.settings or {}).get("daily_budget_usd", DEFAULT_DAILY_BUDGET_USD))
    if spent_today(session, client.id) >= budget:
        raise InvalidState(f"today's model budget for this client (${budget:.2f}) is used up")
    if spend.left(session, scope.org_id) <= 0:
        raise InvalidState(spend.cap_reason())
    attempts = session.scalar(select(func.count()).select_from(m.Execution).where(
        m.Execution.task_id == task.id, m.Execution.purpose == purpose)) or 0
    ex_name, model, effort = route(session, task, purpose, prefer=executor)
    role = "qa" if purpose == "qa" else task.owner_role
    agent_scope = _agent_scope(session, scope, role)
    if purpose == "qa":  # QA re-checks everything against the latest work
        task.checklist = [{**i, "passed": False, "by": None} for i in task.checklist]
    pk, h = packets.build(session, agent_scope, task, purpose, agent_role=role, feedback=feedback, approval=approval)
    pk["routing"] = {"executor": ex_name, "model": model, "effort": effort}
    ex = m.Execution(org_id=scope.org_id, client_id=task.client_id, task_id=task.id, purpose=purpose, executor=ex_name,
                     model=model, agent_role=role, attempt=attempts + 1, packet=pk, packet_hash=h,
                     requested_by=scope.actor.ref)
    session.add(ex)
    if purpose == "produce" and task.status in ("todo", "blocked", "review"):
        task.status = "in_progress"
    events.record(session, scope, "execution.queued", ex, f"{purpose} → {ex_name} {model}".strip(),
                  {"task": task.id, "executor": ex_name, "model": model, "attempt": ex.attempt})
    return ex


def claim(factory: sessionmaker[Session], executors: Optional[set[str]] = None) -> Optional[str]:
    """Take the oldest queued execution whose client has nothing running (one execution per brand at a time)."""
    with factory() as s:
        busy = set(s.scalars(select(m.Execution.client_id).where(m.Execution.status == "running")))
        q = select(m.Execution).where(m.Execution.status == "queued").order_by(m.Execution.created_at).limit(20)
        for ex in s.scalars(q).all():
            if ex.client_id in busy or (executors and ex.executor not in executors):
                continue
            res = s.execute(update(m.Execution).where(m.Execution.id == ex.id, m.Execution.status == "queued")
                            .values(status="running", started_at=utcnow()))
            if res.rowcount == 1:
                s.commit()
                return ex.id
        return None


def run(factory: sessionmaker[Session], execution_id: str, adapters) -> m.Execution:
    """adapters: {executor: Adapter}, or a callable org_id -> that dict (one key set per org)."""
    with factory() as s:
        ex = s.get(m.Execution, execution_id)
        if callable(adapters):
            adapters = adapters(ex.org_id)
        task = s.get(m.Task, ex.task_id)
        base = scope_for_agent(s, _agent_by_role(s, ex.org_id, ex.agent_role), ex.client_id)
        gw = Gateway(s, base, ex, task)
        try:
            adapter = adapters.get(ex.executor)
            if adapter is None:
                raise ExecutorUnavailable(f"no {ex.executor} executor is configured on this worker")
            client = s.get(m.Client, ex.client_id)
            if ex.executor not in allowed_providers(client) and ex.executor != "human":
                raise ExecutorUnavailable(f"{ex.executor} isn't an allowed provider for this client")
            result: AdapterResult = adapter.run(ex.packet, tools_for(ex.purpose), gw, ex.model, ex.packet["routing"]["effort"])
            ex.input_tokens, ex.output_tokens, ex.cost_usd = result.input_tokens, result.output_tokens, round(result.cost_usd, 6)
            spend.charge(s, ex.org_id, ex.cost_usd)
            ex.result = {"summary": result.summary, "outputs": gw.outputs, "checks": gw.checks, "turns": result.turns}
            ex.finished_at = utcnow()
            _settle(s, base, ex, task, gw)
            s.commit()
        except Exception as e:
            s.rollback()
            ex = s.get(m.Execution, execution_id)
            ex.status, ex.finished_at = "failed", utcnow()
            ex.error = (f"{type(e).__name__}: {e}" if isinstance(e, ExecutorUnavailable)
                        else f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}")[:4000]
            sc = Scope(ex.org_id, ex.client_id, base.actor)
            events.record(s, sc, "execution.failed", ex, str(e)[:300])
            s.commit()
        return ex


def _agent_by_role(s: Session, org_id: str, role: str) -> m.Agent:
    a = s.scalar(select(m.Agent).where(m.Agent.org_id == org_id, m.Agent.role == role, m.Agent.active.is_(True)))
    if a is None:
        raise NotPermitted(f"no active {role} agent")
    return a


def _settle(s: Session, scope: Scope, ex: m.Execution, task: m.Task, gw: Gateway) -> None:
    """Move the task forward based on what the execution produced."""
    if ex.purpose == "produce":
        if gw.awaiting:
            ex.status, ex.waiting_on = "waiting_approval", gw.awaiting
            operations.set_task_status(s, scope, task.id, "blocked", "waiting for an approval")
            events.record(s, scope, "execution.waiting", ex, f"waiting on approval {gw.awaiting}")
            return
        if not gw.outputs:
            raise RuntimeError("the executor finished without saving any output")
        task.outputs = list(task.outputs or []) + gw.outputs
        ex.status = "succeeded"
        operations.set_task_status(s, scope, task.id, "review", f"{len(gw.outputs)} output(s) ready for QA")
        events.record(s, scope, "execution.succeeded", ex, ex.result.get("summary", "")[:300])
        dispatch(s, scope, task.id, purpose="qa")
        return
    # QA
    ex.status = "succeeded"
    events.record(s, scope, "execution.succeeded", ex, ex.result.get("summary", "")[:300])
    s.flush()
    s.refresh(task)
    failed = [i for i in task.checklist if not i["passed"]]
    blockers = [f for f in gw.flagged if f["severity"] in RELEASE_BLOCKING]
    if blockers:
        # A serious problem outside the checklist stops release even when every listed check passed.
        ex.result = {**ex.result, "release_ready": False, "blockers": blockers}
        lines = "; ".join(f"{b['severity']}: {b['title']} (insight {b['id']})" for b in blockers)
        operations.set_task_status(s, scope, task.id, "blocked", f"NOT RELEASE-READY: unresolved blocker(s): {lines}")
        return
    if not failed:
        owner = _agent_scope(s, scope, task.owner_role)
        operations.complete_task(s, owner, task.id, task.outputs)
        return
    notes = {c["item_id"]: c["note"] for c in gw.checks}
    feedback = "\n".join(f"- {i['text']}: {notes.get(i['id'], 'not checked')}" for i in failed)
    produced = s.scalar(select(func.count()).select_from(m.Execution).where(
        m.Execution.task_id == task.id, m.Execution.purpose == "produce")) or 0
    if produced >= MAX_ATTEMPTS:
        operations.set_task_status(s, scope, task.id, "blocked", f"QA failed {produced} times; needs a person:\n{feedback}")
        return
    operations.set_task_status(s, scope, task.id, "in_progress", "QA asked for changes")
    dispatch(s, scope, task.id, purpose="produce", feedback=feedback)


@approvals.on_decided("execution")
def _execution_decided(session: Session, scope: Scope, ap: m.Approval, approved: bool) -> None:
    ex = get_scoped(session, scope, m.Execution, ap.subject_id)
    if ex.status != "waiting_approval" or ex.waiting_on != ap.id:
        return
    ex.status = "succeeded" if approved else "cancelled"
    task = session.get(m.Task, ex.task_id)
    owner = _agent_scope(session, scope, task.owner_role)
    if approved:
        dispatch(session, owner, task.id, purpose="produce", approval=ap)
    else:
        operations.set_task_status(session, owner, task.id, "blocked", f"approval rejected: {ap.note or ap.summary}")


def work(factory: sessionmaker[Session], adapters, limit: int = 20) -> list[m.Execution]:
    done = []
    for _ in range(limit):
        ex_id = claim(factory, None if callable(adapters) else set(adapters))
        if ex_id is None:
            break
        done.append(run(factory, ex_id, adapters))
    return done
