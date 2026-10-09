"""The always-on loop: job definitions, the scheduler and the runner.

A job definition is trigger -> checks -> action -> owner role -> completion condition.
The scheduler turns due definitions into job runs with a unique run_key per period, so a run
happens once however many schedulers race. The runner claims one queued run at a time,
executes it as the owner role's agent, and records what it produced. A run that produces
nothing must say why (status no_action), so observation without execution is visible.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Literal, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from . import events
from . import models as m
from .db import utcnow
from .policy import authorize
from .scope import NotPermitted, Scope, scope_for_agent

Cadence = Literal["daily", "weekly", "biweekly", "monthly", "quarterly", "event"]


@dataclass
class JobContext:
    session: Session
    scope: Scope
    run: m.JobRun
    services: dict  # injected dependencies (vault, connector factories, clock)
    produced: dict = field(default_factory=lambda: {"insights": [], "tasks": [], "approvals": [], "results": []})

    def note(self, kind: str, obj_id: str) -> None:
        self.produced.setdefault(kind, []).append(obj_id)


@dataclass
class JobOutcome:
    summary: str
    no_action_reason: str = ""  # required when nothing was produced


@dataclass(frozen=True)
class JobDefinition:
    key: str
    title: str
    cadence: Cadence
    owner_role: str
    checks: str                 # what the job inspects
    action: str                 # what it does with what it finds
    completion: str             # how we know the run is done
    handler: Optional[Callable[[JobContext], JobOutcome]] = None
    events: tuple[str, ...] = ()  # for cadence == "event"
    area: str = "reporting"


REGISTRY: dict[str, JobDefinition] = {}


def register(defn: JobDefinition) -> JobDefinition:
    if defn.key in REGISTRY:
        raise ValueError(f"job {defn.key} registered twice")
    REGISTRY[defn.key] = defn
    return defn


def job(key: str, title: str, cadence: Cadence, owner_role: str, *, checks: str, action: str,
        completion: str, events: tuple[str, ...] = (), area: str = "reporting"):
    """Decorator: register a handler as a job definition."""
    def deco(fn: Callable[[JobContext], JobOutcome]):
        register(JobDefinition(key, title, cadence, owner_role, checks, action, completion, fn, events, area))
        return fn
    return deco


# ---------------------------------------------------------------- scheduler

def period_key(cadence: Cadence, now: datetime, tz: str) -> Optional[str]:
    local = now.astimezone(ZoneInfo(tz))
    iso = local.isocalendar()
    return {
        "daily": local.strftime("%Y-%m-%d"),
        "weekly": f"{iso.year}-W{iso.week:02d}",
        "biweekly": f"{iso.year}-B{(iso.week - 1) // 2 + 1:02d}",
        "monthly": local.strftime("%Y-%m"),
        "quarterly": f"{local.year}-Q{(local.month - 1) // 3 + 1}",
    }.get(cadence)


def enqueue_due(session: Session, now: Optional[datetime] = None, org_id: Optional[str] = None) -> list[m.JobRun]:
    """Queue every scheduled job whose period has no run yet, for every active client."""
    now = now or utcnow()
    q = select(m.Client).where(m.Client.status.in_(("onboarding", "active")))
    if org_id:
        q = q.where(m.Client.org_id == org_id)
    created = []
    for client in session.scalars(q):
        for defn in REGISTRY.values():
            if defn.cadence == "event" or defn.handler is None:
                continue
            pk = period_key(defn.cadence, now, client.timezone)
            run = _enqueue(session, client, defn, f"{defn.key}:{client.id}:{pk}", now, {"kind": "schedule", "period": pk})
            if run:
                created.append(run)
    return created


def emit(session: Session, scope: Scope, event_name: str, payload: dict, dedupe: str) -> list[m.JobRun]:
    """Queue every job listening for an event (campaign.sent, metric.anomaly, ...)."""
    client = session.get(m.Client, scope.require_client())
    out = []
    for defn in REGISTRY.values():
        if defn.cadence == "event" and event_name in defn.events and defn.handler is not None:
            run = _enqueue(session, client, defn, f"{defn.key}:{client.id}:{event_name}:{dedupe}", utcnow(),
                           {"kind": "event", "event": event_name, "payload": payload})
            if run:
                out.append(run)
    return out


def _enqueue(session, client, defn, run_key, due_at, trigger) -> Optional[m.JobRun]:
    if session.scalar(select(m.JobRun.id).where(m.JobRun.run_key == run_key)):
        return None
    run = m.JobRun(org_id=client.org_id, client_id=client.id, job_key=defn.key, run_key=run_key,
                   due_at=due_at, trigger=trigger)
    try:
        with session.begin_nested():
            session.add(run)
    except IntegrityError:  # another scheduler queued it first
        return None
    return run


# ---------------------------------------------------------------- runner

def _agent_for(session: Session, org_id: str, role: str) -> m.Agent:
    agent = session.scalar(select(m.Agent).where(m.Agent.org_id == org_id, m.Agent.role == role, m.Agent.active.is_(True)))
    if agent is None:
        raise NotPermitted(f"no active {role} agent in this organization")
    return agent


def claim(factory: sessionmaker[Session], job_key: Optional[str] = None) -> Optional[str]:
    """Atomically move one queued run to running. Returns its id, or None."""
    with factory() as s:
        q = select(m.JobRun.id).where(m.JobRun.status == "queued").order_by(m.JobRun.due_at).limit(5)
        if job_key:
            q = q.where(m.JobRun.job_key == job_key)
        for run_id in s.scalars(q).all():
            res = s.execute(update(m.JobRun).where(m.JobRun.id == run_id, m.JobRun.status == "queued")
                            .values(status="running", started_at=utcnow(), attempts=m.JobRun.attempts + 1))
            if res.rowcount == 1:
                s.commit()
                return run_id
        return None


def execute(factory: sessionmaker[Session], run_id: str, services: Optional[dict] = None) -> m.JobRun:
    """Run one claimed job in its own transaction. Failures are recorded, never swallowed silently."""
    services = services or {}
    with factory() as s:
        run = s.get(m.JobRun, run_id)
        defn = REGISTRY.get(run.job_key)
        try:
            if defn is None or defn.handler is None:
                raise NotPermitted(f"job {run.job_key} has no handler in this build")
            scope = scope_for_agent(s, _agent_for(s, run.org_id, defn.owner_role), run.client_id)
            auth = authorize(s, scope, "job.run")
            if not auth.allowed:
                raise NotPermitted(auth.reason)
            ctx = JobContext(s, scope, run, services)
            outcome = defn.handler(ctx)
            produced_any = any(ctx.produced.get(k) for k in ctx.produced)
            if not produced_any and not outcome.no_action_reason:
                raise ValueError(f"job {defn.key} produced nothing and gave no reason")
            run.status = "succeeded" if produced_any else "no_action"
            run.summary = outcome.summary if produced_any else f"{outcome.summary} ({outcome.no_action_reason})"
            run.produced, run.finished_at = ctx.produced, utcnow()
            events.record(s, scope, f"job.{run.status}", run, run.summary, {"job": defn.key, "produced": ctx.produced})
            s.commit()
        except Exception as e:
            s.rollback()
            run = s.get(m.JobRun, run_id)
            run.status, run.finished_at = "failed", utcnow()
            run.error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}"[:4000]
            s.commit()
        return run


def run_pending(factory: sessionmaker[Session], services: Optional[dict] = None, limit: int = 50) -> list[m.JobRun]:
    done = []
    for _ in range(limit):
        run_id = claim(factory)
        if run_id is None:
            break
        done.append(execute(factory, run_id, services))
    return done


def requeue_stuck(session: Session, older_than: timedelta = timedelta(hours=1), max_attempts: int = 3) -> int:
    """A worker that died mid-run leaves it 'running'. Put it back, up to max_attempts."""
    cutoff = utcnow() - older_than
    res = session.execute(update(m.JobRun).where(m.JobRun.status == "running", m.JobRun.started_at < cutoff,
                                                 m.JobRun.attempts < max_attempts).values(status="queued"))
    return res.rowcount
