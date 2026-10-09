"""Routes under /v1/clients/{client_slug}/... Every one runs with a Scope built by scope_for_user
for the slug resolved inside the caller's organization; a client the caller can't see is a 404."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from .. import approvals, connections, events, knowledge, operations, policy
from .. import models as m
from ..scope import NotFound, get_scoped
from . import schemas as s
from .deps import ClientScope, ServiceUnavailable, SessionDep, require, service

router = APIRouter(prefix="/v1/clients/{client_slug}")


def _client_slug(session, scope) -> str:
    return session.get(m.Client, scope.client_id).slug


# ---------------------------------------------------------------- knowledge (layer 2)

@router.get("/facts", response_model=list[s.FactOut])
def list_facts(session: SessionDep, scope: ClientScope, include_proposed: bool = False, area: Optional[str] = None):
    return [s.FactOut.model_validate(f) for f in
            knowledge.facts(session, scope, area=area, include_proposed=include_proposed)]


@router.post("/facts", response_model=s.FactProposedOut, status_code=201)
def propose_fact(body: s.FactIn, session: SessionDep, scope: ClientScope):
    fact, ap = knowledge.propose_fact(session, scope, area=body.area, component=body.component, key=body.key,
                                      title=body.title, value=body.value, body=body.body, source_kind=body.source_kind,
                                      source_ref=body.source_ref, review_by=body.review_by)
    session.flush()
    return s.FactProposedOut(fact=s.FactOut.model_validate(fact), approval=s.ApprovalOut.model_validate(ap))


@router.get("/context/{area}")
def context_pack(area: str, session: SessionDep, scope: ClientScope) -> dict:
    return knowledge.context_pack(session, scope, area)


# ---------------------------------------------------------------- approvals

@router.get("/approvals", response_model=list[s.ApprovalOut])
def pending_approvals(session: SessionDep, scope: ClientScope):
    require(session, scope, "data.read")
    return [s.ApprovalOut.model_validate(a) for a in approvals.pending(session, scope)]


@router.post("/approvals/{approval_id}/decide", response_model=s.ApprovalOut)
def decide(approval_id: str, body: s.DecideIn, session: SessionDep, scope: ClientScope):
    ap = approvals.decide(session, scope, approval_id, body.approve, body.note)
    session.flush()
    return s.ApprovalOut.model_validate(ap)


# ---------------------------------------------------------------- insights

@router.get("/insights", response_model=list[s.InsightOut])
def list_insights(session: SessionDep, scope: ClientScope, status: Optional[str] = None):
    require(session, scope, "data.read")
    q = select(m.Insight).where(m.Insight.org_id == scope.org_id, m.Insight.client_id == scope.client_id)
    if status:
        q = q.where(m.Insight.status == status)
    return [s.InsightOut.model_validate(i) for i in session.scalars(q.order_by(m.Insight.created_at.desc()))]


@router.post("/insights", response_model=s.InsightCreatedOut, status_code=201)
def create_insight(body: s.InsightIn, session: SessionDep, scope: ClientScope):
    ins, created = operations.create_insight(
        session, scope, area=body.area, title=body.title, evidence=[e.model_dump() for e in body.evidence],
        summary=body.summary, severity=body.severity, source_kind=body.source_kind, source_ref=body.source_ref,
        dedupe_key=body.dedupe_key)
    session.flush()
    return s.InsightCreatedOut(insight=s.InsightOut.model_validate(ins), created=created)


@router.post("/insights/{insight_id}/dismiss", response_model=s.InsightOut)
def dismiss_insight(insight_id: str, body: s.DismissIn, session: SessionDep, scope: ClientScope):
    return s.InsightOut.model_validate(operations.dismiss_insight(session, scope, insight_id, body.reason))


# ---------------------------------------------------------------- decisions

@router.post("/decisions", response_model=s.DecisionProposedOut, status_code=201)
def propose_decision(body: s.DecisionIn, session: SessionDep, scope: ClientScope):
    d, ap = operations.propose_decision(session, scope, title=body.title, insight_ids=body.insight_ids,
                                        rationale=body.rationale, measurement=body.measurement.model_dump(),
                                        plan_scope=body.plan_scope)
    session.flush()
    return s.DecisionProposedOut(decision=s.DecisionOut.model_validate(d), approval=s.ApprovalOut.model_validate(ap))


@router.get("/decisions/{decision_id}")
def decision_chain(decision_id: str, session: SessionDep, scope: ClientScope) -> dict:
    require(session, scope, "data.read")
    return operations.chain(session, scope, decision_id)


# ---------------------------------------------------------------- tasks

@router.get("/tasks", response_model=list[s.TaskOut])
def list_tasks(session: SessionDep, scope: ClientScope, status: Optional[str] = None,
               decision_id: Optional[str] = None):
    require(session, scope, "data.read")
    q = select(m.Task).where(m.Task.org_id == scope.org_id, m.Task.client_id == scope.client_id)
    if status:
        q = q.where(m.Task.status == status)
    if decision_id:
        q = q.where(m.Task.decision_id == decision_id)
    return [s.TaskOut.model_validate(t) for t in session.scalars(q.order_by(m.Task.created_at))]


@router.post("/tasks", response_model=s.TaskOut, status_code=201)
def create_task(body: s.TaskIn, session: SessionDep, scope: ClientScope):
    t = operations.create_task(session, scope, title=body.title, area=body.area, owner_role=body.owner_role,
                               done_when=body.done_when, checklist=body.checklist, decision_id=body.decision_id,
                               job_run_id=body.job_run_id, due_date=body.due_date, assignee_id=body.assignee_id,
                               phase=body.phase)
    session.flush()
    return s.TaskOut.model_validate(t)


@router.post("/tasks/{task_id}/status", response_model=s.TaskOut)
def task_status(task_id: str, body: s.TaskStatusIn, session: SessionDep, scope: ClientScope):
    return s.TaskOut.model_validate(operations.set_task_status(session, scope, task_id, body.status, body.note))


@router.post("/tasks/{task_id}/checks/{item_id}", response_model=s.TaskOut)
def task_check(task_id: str, item_id: str, session: SessionDep, scope: ClientScope, body: Optional[s.CheckIn] = None):
    passed = body.passed if body is not None else True
    return s.TaskOut.model_validate(operations.pass_check(session, scope, task_id, item_id, passed))


@router.post("/tasks/{task_id}/complete", response_model=s.TaskCompletedOut)
def complete_task(task_id: str, body: s.CompleteIn, session: SessionDep, scope: ClientScope):
    t, r = operations.complete_task(session, scope, task_id, [o.model_dump() for o in body.outputs])
    session.flush()
    return s.TaskCompletedOut(task=s.TaskOut.model_validate(t), result=s.ResultOut.model_validate(r) if r else None)


# ---------------------------------------------------------------- results

@router.get("/results", response_model=list[s.ResultOut])
def list_results(session: SessionDep, scope: ClientScope, due: bool = False):
    require(session, scope, "data.read")
    if due:
        rows = operations.due_results(session, scope)
    else:
        rows = session.scalars(select(m.Result).where(m.Result.org_id == scope.org_id,
                                                      m.Result.client_id == scope.client_id)
                               .order_by(m.Result.window_end)).all()
    return [s.ResultOut.model_validate(r) for r in rows]


@router.post("/results/{result_id}", response_model=s.ResultOut)
def record_result(result_id: str, body: s.ResultIn, session: SessionDep, scope: ClientScope):
    # No `today` parameter over HTTP: the measurement window is judged by the server's clock.
    r = operations.record_result(session, scope, result_id, observed=body.observed, notes=body.notes,
                                 inconclusive=body.inconclusive)
    return s.ResultOut.model_validate(r)


# ---------------------------------------------------------------- connections

@router.get("/connections", response_model=list[s.ConnectionOut])
def list_connections(session: SessionDep, scope: ClientScope):
    require(session, scope, "data.read")
    rows = session.scalars(select(m.Connection).where(m.Connection.org_id == scope.org_id,
                                                      m.Connection.client_id == scope.client_id)
                           .order_by(m.Connection.created_at)).all()
    return [s.ConnectionOut.model_validate(c) for c in rows]


@router.post("/connections", response_model=s.ConnectionOut, status_code=201)
def add_connection(body: s.ConnectionIn, request: Request, session: SessionDep, scope: ClientScope):
    if body.provider not in connections.PROVIDERS:
        raise ValueError(f"provider must be one of {connections.PROVIDERS}")
    # Ask the policy engine before the key is sent anywhere; connections.add checks (and logs) again.
    require(session, scope, "connection.manage")
    verify = service(request, f"{body.provider}_verify")
    vault = service(request, "vault")
    api_key = body.api_key.get_secret_value()
    try:
        account = verify(api_key)
    except Exception as e:  # never echo the provider's message: it may quote the key
        raise ValueError(f"{body.provider} didn't accept the key or couldn't be reached ({type(e).__name__})") from None
    account = account or {}
    c = connections.add(session, scope, vault, provider=body.provider,
                        external_account_id=str(account.get("id") or ""), secret=api_key,
                        label=body.label or str(account.get("name") or ""))
    session.flush()
    return s.ConnectionOut.model_validate(c)


@router.delete("/connections/{connection_id}", response_model=s.ConnectionOut)
def revoke_connection(connection_id: str, session: SessionDep, scope: ClientScope,
                      reason: str = "revoked through the API"):
    return s.ConnectionOut.model_validate(connections.revoke(session, scope, connection_id, reason))


# ---------------------------------------------------------------- policy dry run

@router.post("/authorize", response_model=s.AuthorizeOut)
@router.post("/policy/authorize", response_model=s.AuthorizeOut, include_in_schema=False)
def authorize_dry_run(body: s.AuthorizeIn, session: SessionDep, scope: ClientScope):
    """What would happen if the caller did this now. Never consumes an approval, never logs."""
    auth = policy.authorize(session, scope, body.action, decision_id=body.decision_id,
                            approval_id=body.approval_id, payload=body.payload, consume=False, log=False)
    return s.AuthorizeOut(outcome=auth.outcome, action=auth.action, level=auth.level, reason=auth.reason,
                          decision_id=auth.decision_id, approval_id=auth.approval_id)


# ---------------------------------------------------------------- jobs

@router.get("/job-runs", response_model=list[s.JobRunOut])
def job_runs(session: SessionDep, scope: ClientScope, status: Optional[str] = None,
             limit: int = Query(50, ge=1, le=500)):
    require(session, scope, "data.read")
    q = select(m.JobRun).where(m.JobRun.org_id == scope.org_id, m.JobRun.client_id == scope.client_id)
    if status:
        q = q.where(m.JobRun.status == status)
    return [s.JobRunOut.model_validate(r) for r in session.scalars(q.order_by(m.JobRun.due_at.desc()).limit(limit))]


# ---------------------------------------------------------------- audits

SNAPSHOT_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def resolve_snapshot(root: Path, snapshot_slug: str, client_slug: str) -> Path:
    """A snapshot folder strictly inside root, named for this client, with no symlink leading out.

    Rejects separators, '..', absolute paths and any symlink (the folder itself or a file in it)
    whose target lies outside the root. The client-prefix rule stops one client's audit from
    reading another client's snapshot (slugs are `<client-slug>-<yyyy-mm-dd>`).
    """
    if not SNAPSHOT_SLUG.match(snapshot_slug) or ".." in snapshot_slug:
        raise ValueError("snapshot_slug must be one folder name inside the audit output root")
    if snapshot_slug != client_slug and not snapshot_slug.startswith(client_slug + "-"):
        raise ValueError(f"snapshot_slug must be this client's snapshot ({client_slug} or {client_slug}-<date>)")
    try:
        real_root = Path(root).resolve(strict=True)
    except FileNotFoundError:
        raise ServiceUnavailable("the audit output root doesn't exist on this server") from None
    candidate = real_root / snapshot_slug
    try:
        target = candidate.resolve(strict=True)
    except FileNotFoundError:
        raise NotFound("snapshot not found") from None
    if target == real_root or not target.is_relative_to(real_root):
        raise ValueError("snapshot_slug points outside the audit output root")
    real_name = target.relative_to(real_root).parts[0]
    if real_name != client_slug and not real_name.startswith(client_slug + "-"):
        raise ValueError("snapshot_slug links to another client's snapshot")
    if not target.is_dir():
        raise NotFound("snapshot not found")
    for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            if p.is_symlink() and not p.resolve().is_relative_to(real_root):
                raise ValueError("the snapshot contains a link that points outside the audit output root")
    return target


def _scorecard_out(sc: m.Scorecard) -> s.ScorecardDetailOut:
    out = s.ScorecardOut.model_validate(sc).model_dump()
    return s.ScorecardDetailOut(**out, results=[s.CheckResultOut.model_validate(r) for r in sc.results])


@router.post("/audits", response_model=s.ScorecardDetailOut, status_code=201)
def run_audit(body: s.AuditIn, request: Request, session: SessionDep, scope: ClientScope):
    require(session, scope, "analysis.run")  # fail before touching the filesystem
    snapshot_dir = resolve_snapshot(service(request, "audit_output_root"), body.snapshot_slug,
                                    _client_slug(session, scope))
    engine = service(request, "run_audit")
    sc = engine(session, scope, snapshot_dir, body.track)
    session.flush()
    return _scorecard_out(sc)


@router.get("/scorecards/{scorecard_id}", response_model=s.ScorecardDetailOut)
def get_scorecard(scorecard_id: str, session: SessionDep, scope: ClientScope):
    require(session, scope, "data.read")
    return _scorecard_out(get_scoped(session, scope, m.Scorecard, scorecard_id))


@router.get("/scorecards/{before_id}/compare/{after_id}")
def compare_scorecards(before_id: str, after_id: str, request: Request, session: SessionDep,
                       scope: ClientScope) -> dict:
    require(session, scope, "data.read")
    for sc_id in (before_id, after_id):
        get_scoped(session, scope, m.Scorecard, sc_id)  # both must belong to this client
    return service(request, "compare_audits")(session, scope, before_id, after_id)


# ---------------------------------------------------------------- events

@router.get("/events", response_model=list[s.EventOut])
def list_events(session: SessionDep, scope: ClientScope, limit: int = Query(50, ge=1, le=500)):
    require(session, scope, "data.read")
    q = (select(m.Event).where(m.Event.org_id == scope.org_id, m.Event.chain == scope.client_id)
         .order_by(m.Event.seq.desc()).limit(limit))
    return [s.EventOut.model_validate(e) for e in session.scalars(q)]


@router.get("/events/verify", response_model=s.ChainOut)
def verify_events(session: SessionDep, scope: ClientScope):
    require(session, scope, "data.read")
    ok, broken = events.verify_chain(session, scope.org_id, scope.client_id)
    return s.ChainOut(ok=ok, broken_at=broken, count=events.count(session, scope.org_id, scope.client_id))
