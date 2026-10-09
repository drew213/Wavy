"""Layer 3: the operating chain. Insight -> Decision -> Task -> Result.

The rules the founder's spec asks for are enforced here, not left to discipline:
- an insight needs evidence;
- a decision needs at least one insight, a measurement plan, and an approval;
- a task needs an approved decision (or a standing job run);
- a task can't be done until its checklist passes and its outputs are recorded;
- finishing a decision task opens a pending result; the decision closes when every result is in.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import approvals, events
from . import models as m
from .db import utcnow
from .policy import ACTIONS, authorize
from .scope import InvalidState, NotPermitted, Scope, get_scoped
from .taxonomy import require_area


def _need(session, scope, action, **kw):
    auth = authorize(session, scope, action, **kw)
    if not auth.allowed:
        raise NotPermitted(auth.reason)
    return auth


# ---------------------------------------------------------------- insights

def create_insight(session: Session, scope: Scope, *, area: str, title: str, evidence: list[dict],
                   summary: str = "", severity: str = "medium", source_kind: str = "agent",
                   source_ref: str = "", dedupe_key: Optional[str] = None) -> tuple[m.Insight, bool]:
    """Returns (insight, created). An open insight with the same dedupe_key is returned instead of a duplicate."""
    client_id = scope.require_client()
    _need(session, scope, "insight.create")
    require_area(area)
    if severity not in m.SEVERITIES:
        raise ValueError(f"severity must be one of {m.SEVERITIES}")
    if not evidence or not all(isinstance(e, dict) and e.get("ref") for e in evidence):
        raise ValueError("an insight needs evidence: a list of {kind, ref, detail} with a ref")
    if dedupe_key:
        existing = session.scalar(select(m.Insight).where(
            m.Insight.client_id == client_id, m.Insight.dedupe_key == dedupe_key, m.Insight.status == "open"))
        if existing is not None:
            return existing, False
    ins = m.Insight(org_id=scope.org_id, client_id=client_id, area=area, title=title, summary=summary,
                    severity=severity, evidence=evidence, source_kind=source_kind, source_ref=source_ref,
                    dedupe_key=dedupe_key, created_by=scope.actor.ref)
    session.add(ins)
    events.record(session, scope, "insight.created", ins, title, {"severity": severity, "area": area})
    return ins, True


def dismiss_insight(session: Session, scope: Scope, insight_id: str, reason: str) -> m.Insight:
    _need(session, scope, "task.manage")
    ins = get_scoped(session, scope, m.Insight, insight_id)
    if ins.status != "open":
        raise InvalidState(f"insight is {ins.status}")
    if not reason.strip():
        raise ValueError("say why it's dismissed")
    ins.status = "dismissed"
    events.record(session, scope, "insight.dismissed", ins, reason)
    return ins


# ---------------------------------------------------------------- decisions

def propose_decision(session: Session, scope: Scope, *, title: str, insight_ids: list[str], rationale: str,
                     measurement: dict, plan_scope: Optional[list[str]] = None) -> tuple[m.Decision, m.Approval]:
    """measurement = {"metric": str, "window_days": int, "baseline": float|None}."""
    scope.require_client()
    _need(session, scope, "decision.propose")
    if not insight_ids:
        raise ValueError("a decision has to answer at least one insight")
    if not measurement.get("metric") or int(measurement.get("window_days") or 0) <= 0:
        raise ValueError("a decision needs a measurement plan: metric and window_days")
    plan_scope = plan_scope or []
    for a in plan_scope:
        if a not in ACTIONS or ACTIONS[a].level != "approved_plan":
            raise ValueError(f"{a} can't be authorized by a plan (only approved_plan actions can)")
    found = [get_scoped(session, scope, m.Insight, i) for i in insight_ids]
    d = m.Decision(org_id=scope.org_id, client_id=scope.client_id, title=title, rationale=rationale,
                   scope=plan_scope, measurement=measurement, proposed_by=scope.actor.ref)
    d.insights = found
    session.add(d)
    events.record(session, scope, "decision.proposed", d, title, {"insights": insight_ids, "scope": plan_scope})
    ap = approvals.request(session, scope, "decision.approve", "decision", d.id, f"Approve plan: {title}",
                           {"title": title, "rationale": rationale, "scope": plan_scope, "measurement": measurement,
                            "insights": insight_ids})
    d.approval_id = ap.id
    return d, ap


@approvals.on_decided("decision")
def _decision_decided(session: Session, scope: Scope, ap: m.Approval, approved: bool) -> None:
    d = get_scoped(session, scope, m.Decision, ap.subject_id)
    if d.status != "proposed":
        raise InvalidState(f"decision is already {d.status}")
    d.status = "approved" if approved else "rejected"
    if approved:
        for ins in d.insights:
            if ins.status == "open":
                ins.status = "actioned"
    events.record(session, scope, f"decision.{d.status}", d, d.title)


# ---------------------------------------------------------------- tasks

def create_task(session: Session, scope: Scope, *, title: str, area: str, owner_role: str, done_when: str,
                checklist: list[str], decision_id: Optional[str] = None, job_run_id: Optional[str] = None,
                due_date: Optional[date] = None, assignee_id: Optional[str] = None) -> m.Task:
    scope.require_client()
    _need(session, scope, "task.manage")
    require_area(area)
    if bool(decision_id) == bool(job_run_id):
        raise ValueError("a task comes from exactly one approved decision or one job run")
    if decision_id:
        d = get_scoped(session, scope, m.Decision, decision_id)
        if d.status != "approved":
            raise InvalidState(f"tasks can only come from an approved decision; this one is {d.status}")
    else:
        get_scoped(session, scope, m.JobRun, job_run_id)
    if not done_when.strip():
        raise ValueError("say what done means (done_when)")
    if not checklist:
        raise ValueError("a task needs a quality checklist")
    t = m.Task(org_id=scope.org_id, client_id=scope.client_id, decision_id=decision_id, job_run_id=job_run_id,
               title=title, area=area, owner_role=owner_role, assignee_id=assignee_id, due_date=due_date,
               done_when=done_when, created_by=scope.actor.ref,
               checklist=[{"id": f"c{i + 1}", "text": text, "passed": False, "by": None} for i, text in enumerate(checklist)])
    session.add(t)
    events.record(session, scope, "task.created", t, title, {"decision": decision_id, "job_run": job_run_id})
    return t


def set_task_status(session: Session, scope: Scope, task_id: str, status: str, note: str = "") -> m.Task:
    _need(session, scope, "task.manage")
    t = get_scoped(session, scope, m.Task, task_id)
    if status not in m.TASK_STATUSES or status == "done":
        raise ValueError("use complete_task to finish a task" if status == "done" else f"unknown status {status}")
    if t.status in ("done", "cancelled"):
        raise InvalidState(f"task is {t.status}")
    before, t.status = t.status, status
    events.record(session, scope, "task.status", t, note or f"{before} -> {status}", {"from": before, "to": status})
    return t


def pass_check(session: Session, scope: Scope, task_id: str, item_id: str, passed: bool = True) -> m.Task:
    """QA marks a checklist item. Whoever created the task can't pass its own checks."""
    _need(session, scope, "task.manage")
    t = get_scoped(session, scope, m.Task, task_id)
    if passed and t.created_by == scope.actor.ref:
        raise NotPermitted("the task's author can't pass its own quality checks")
    items = [dict(i) for i in t.checklist]
    hit = next((i for i in items if i["id"] == item_id), None)
    if hit is None:
        raise ValueError(f"no checklist item {item_id}")
    hit["passed"], hit["by"] = passed, scope.actor.ref
    t.checklist = items  # reassign so the JSON column is marked dirty
    events.record(session, scope, "task.check", t, hit["text"], {"item": item_id, "passed": passed})
    return t


def complete_task(session: Session, scope: Scope, task_id: str, outputs: list[dict]) -> tuple[m.Task, Optional[m.Result]]:
    _need(session, scope, "task.manage")
    t = get_scoped(session, scope, m.Task, task_id)
    if t.status in ("done", "cancelled"):
        raise InvalidState(f"task is already {t.status}")
    open_items = [i["text"] for i in t.checklist if not i["passed"]]
    if open_items:
        raise InvalidState("quality checklist isn't passed: " + "; ".join(open_items))
    if not outputs or not all(isinstance(o, dict) and o.get("ref") for o in outputs):
        raise InvalidState("record what the task produced: a list of {kind, ref, label}")
    t.outputs, t.status, t.completed_at = outputs, "done", utcnow()
    events.record(session, scope, "task.done", t, t.title, {"outputs": [o["ref"] for o in outputs]})
    result = None
    if t.decision_id:
        d = session.get(m.Decision, t.decision_id)
        today = utcnow().date()
        result = m.Result(org_id=scope.org_id, client_id=scope.client_id, task_id=t.id, metric=d.measurement["metric"],
                          baseline=d.measurement.get("baseline"), window_start=today,
                          window_end=today + timedelta(days=int(d.measurement["window_days"])))
        session.add(result)
        events.record(session, scope, "result.opened", result, f"measure {result.metric} on {result.window_end}")
    return t, result


# ---------------------------------------------------------------- results

def due_results(session: Session, scope: Scope, today: Optional[date] = None) -> list[m.Result]:
    today = today or utcnow().date()
    return list(session.scalars(select(m.Result).where(
        m.Result.client_id == scope.require_client(), m.Result.status == "pending", m.Result.window_end <= today)))


def record_result(session: Session, scope: Scope, result_id: str, *, observed: Optional[float], notes: str,
                  inconclusive: bool = False, today: Optional[date] = None) -> m.Result:
    _need(session, scope, "result.record")
    r = get_scoped(session, scope, m.Result, result_id)
    today = today or utcnow().date()
    if r.status != "pending":
        raise InvalidState(f"result is already {r.status}")
    if not inconclusive and today < r.window_end:
        raise InvalidState(f"the measurement window runs until {r.window_end}")
    if not inconclusive and observed is None:
        raise ValueError("record the observed value, or mark it inconclusive")
    if not notes.strip():
        raise ValueError("explain the result (notes)")
    r.observed, r.notes, r.status = observed, notes, ("inconclusive" if inconclusive else "measured")
    r.measured_by, r.measured_at = scope.actor.ref, utcnow()
    events.record(session, scope, f"result.{r.status}", r, notes, {"metric": r.metric, "baseline": r.baseline, "observed": observed})
    _maybe_close(session, scope, session.get(m.Task, r.task_id).decision_id)
    return r


def _maybe_close(session: Session, scope: Scope, decision_id: Optional[str]) -> None:
    if not decision_id:
        return
    d = session.get(m.Decision, decision_id)
    tasks = list(session.scalars(select(m.Task).where(m.Task.decision_id == decision_id)))
    if not tasks or any(t.status not in ("done", "cancelled") for t in tasks):
        return
    results = list(session.scalars(select(m.Result).where(m.Result.task_id.in_([t.id for t in tasks]))))
    if any(r.status == "pending" for r in results):
        return
    lines = [f"{r.metric}: {r.baseline} -> {r.observed} ({r.status}) {r.notes}" for r in results]
    d.status, d.closed_at, d.outcome = "closed", utcnow(), "\n".join(lines)
    events.record(session, scope, "decision.closed", d, d.title, {"results": [r.id for r in results]})


def chain(session: Session, scope: Scope, decision_id: str) -> dict:
    """The whole story of one decision, for reports and the learning log."""
    d = get_scoped(session, scope, m.Decision, decision_id)
    tasks = list(session.scalars(select(m.Task).where(m.Task.decision_id == d.id)))
    results = {r.task_id: r for r in session.scalars(select(m.Result).where(m.Result.task_id.in_([t.id for t in tasks])))}
    return {
        "decision": {"id": d.id, "title": d.title, "status": d.status, "measurement": d.measurement, "outcome": d.outcome},
        "insights": [{"id": i.id, "title": i.title, "severity": i.severity, "evidence": i.evidence} for i in d.insights],
        "tasks": [{"id": t.id, "title": t.title, "status": t.status, "outputs": t.outputs,
                   "result": ({"metric": results[t.id].metric, "baseline": results[t.id].baseline,
                               "observed": results[t.id].observed, "status": results[t.id].status}
                              if t.id in results else None)} for t in tasks],
    }
