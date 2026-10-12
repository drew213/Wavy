"""The always-on loop: job definitions, the scheduler and the runner.

A job definition is trigger -> checks -> action -> owner role -> completion condition.
The scheduler turns due definitions into job runs with a unique run_key per period, so a run
happens once however many schedulers race. The runner claims one queued run at a time,
executes it as the owner role's agent, and records what it produced. A run that produces
nothing must say why (status no_action), so observation without execution is visible.
"""
from __future__ import annotations

import os
import threading
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterator, Literal, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from . import events, monitoring
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
    hour: Optional[int] = None    # local hour a scheduled run is due (overnight batches); None = as soon as queued
    family: str = ""              # concurrency family (FAMILY_LIMITS); "" = unlimited


REGISTRY: dict[str, JobDefinition] = {}


def register(defn: JobDefinition) -> JobDefinition:
    if defn.key in REGISTRY:
        raise ValueError(f"job {defn.key} registered twice")
    REGISTRY[defn.key] = defn
    return defn


def job(key: str, title: str, cadence: Cadence, owner_role: str, *, checks: str, action: str,
        completion: str, events: tuple[str, ...] = (), area: str = "reporting", hour: Optional[int] = None,
        family: str = ""):
    """Decorator: register a handler as a job definition."""
    def deco(fn: Callable[[JobContext], JobOutcome]):
        register(JobDefinition(key, title, cadence, owner_role, checks, action, completion, fn, events, area, hour,
                               family))
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


SCHEDULE_BATCH = 100


def due_time(now: datetime, tz: str, hour: Optional[int]) -> datetime:
    """When a scheduled run is due: now, or the next time it is `hour` o'clock in the client's timezone when
    that is later today (a period that opens at midnight runs in the overnight batch, not at the first tick)."""
    if hour is None:
        return now
    local = now.astimezone(ZoneInfo(tz))
    at = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    return at.astimezone(now.tzinfo) if at > local else now


def enqueue_due(session: Session, now: Optional[datetime] = None, org_id: Optional[str] = None) -> list[m.JobRun]:
    """Queue every scheduled job whose period has no run yet, for every active client."""
    now = now or utcnow()
    scheduled = [d for d in REGISTRY.values() if d.cadence != "event" and d.handler is not None]
    if not scheduled:
        return []
    q = select(m.Client).where(m.Client.status.in_(("onboarding", "active"))).order_by(m.Client.id)
    if org_id:
        q = q.where(m.Client.org_id == org_id)
    clients = session.scalars(q).all()
    created = []
    for i in range(0, len(clients), SCHEDULE_BATCH):  # one lookup per batch of clients, not per client and job
        wanted = {}
        for client in clients[i:i + SCHEDULE_BATCH]:
            for defn in scheduled:
                pk = period_key(defn.cadence, now, client.timezone)
                wanted[f"{defn.key}:{client.id}:{pk}"] = (client, defn, pk)
        queued = set(session.scalars(select(m.JobRun.run_key).where(m.JobRun.run_key.in_(list(wanted)))))
        for run_key, (client, defn, pk) in wanted.items():
            if run_key not in queued:
                run = _insert(session, client, defn, run_key, due_time(now, client.timezone, defn.hour),
                              {"kind": "schedule", "period": pk})
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
    return _insert(session, client, defn, run_key, due_at, trigger)


def _insert(session, client, defn, run_key, due_at, trigger) -> Optional[m.JobRun]:
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


# Jobs running at once per family, across every worker: klaviyo jobs spend the brands' API allowances and
# wait on Klaviyo's pacing; analysis jobs are CPU and memory heavy; llm jobs spend model budget. Override with
# WAVYOS_JOB_FAMILY_LIMITS="klaviyo=6,llm=1". The limit is soft: two workers claiming in the same instant can
# each start one more.
FAMILY_LIMITS = {"klaviyo": 4, "analysis": 2, "llm": 2}


def family_limits() -> dict[str, int]:
    limits = dict(FAMILY_LIMITS)
    for part in (os.environ.get("WAVYOS_JOB_FAMILY_LIMITS") or "").split(","):
        name, _, n = part.partition("=")
        if name.strip() and n.strip().isdigit():
            limits[name.strip()] = max(1, int(n))
    return limits


def saturated(session: Session) -> list[str]:
    """Job keys whose family is at its limit right now."""
    limits = family_limits()
    fams = {k: d.family for k, d in REGISTRY.items() if d.family in limits}
    if not fams:
        return []
    running: dict[str, int] = {}
    for key, n in session.execute(select(m.JobRun.job_key, func.count()).where(
            m.JobRun.status == "running", m.JobRun.job_key.in_(list(fams))).group_by(m.JobRun.job_key)):
        running[fams[key]] = running.get(fams[key], 0) + n
    return [k for k, f in fams.items() if running.get(f, 0) >= limits[f]]


def claim(factory: sessionmaker[Session], job_key: Optional[str] = None) -> Optional[str]:
    """Atomically move one queued run to running. Returns its id, or None. Runs of a definition with an
    overnight hour wait until they are due; everything else runs as soon as it is queued. A run whose family
    is at its concurrency limit waits for one of that family to finish."""
    with factory() as s:
        q = select(m.JobRun.id).where(m.JobRun.status == "queued").order_by(m.JobRun.due_at).limit(5)
        held = [k for k, d in REGISTRY.items() if d.hour is not None]
        if held:
            q = q.where(or_(m.JobRun.due_at <= utcnow(), m.JobRun.job_key.not_in(held)))
        full = saturated(s)
        if full:
            q = q.where(m.JobRun.job_key.not_in(full))
        if job_key:
            q = q.where(m.JobRun.job_key == job_key)
        for run_id in s.scalars(q).all():
            now = utcnow()
            res = s.execute(update(m.JobRun).where(m.JobRun.id == run_id, m.JobRun.status == "queued")
                            .values(status="running", started_at=now, heartbeat_at=now, attempts=m.JobRun.attempts + 1))
            if res.rowcount == 1:
                s.commit()
                return run_id
        return None


def execute(factory: sessionmaker[Session], run_id: str, services: Optional[dict] = None) -> m.JobRun:
    """Run one claimed job in its own transaction. Failures are recorded, never swallowed silently."""
    services = services or {}
    with heartbeat(factory, run_id), factory() as s:
        run = s.get(m.JobRun, run_id)
        defn = REGISTRY.get(run.job_key)
        try:
            if defn is None or defn.handler is None:
                raise NotPermitted(f"job {run.job_key} has no handler in this build")
            scope = scope_for_agent(s, _agent_for(s, run.org_id, defn.owner_role), run.client_id)
            auth = authorize(s, scope, "job.run")
            if not auth.allowed:
                raise NotPermitted(auth.reason)
            s.commit()  # release the brand's event chain: its other jobs can't log until this commits
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
            monitoring.report(e)
            s.rollback()
            run = s.get(m.JobRun, run_id)
            run.status, run.finished_at = "failed", utcnow()
            run.error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}"[:4000]
            s.commit()
        return run


def run_pending(factory: sessionmaker[Session], services: Optional[dict] = None, limit: int = 50,
                threads: int = 1) -> list[m.JobRun]:
    """Claim and run queued jobs until none are left or `limit` have run. With threads > 1 several
    run at once (claims are atomic), so one long audit doesn't hold up everything queued behind it."""
    done: list[m.JobRun] = []
    left, lock = [limit], threading.Lock()

    def drain() -> None:
        while True:
            with lock:
                if left[0] <= 0:
                    return
                left[0] -= 1
            run_id = claim(factory)
            if run_id is None:
                return
            r = execute(factory, run_id, services)
            with lock:
                done.append(r)

    if threads <= 1:
        drain()
    else:
        pool = [threading.Thread(target=drain, name=f"wavyos-job-{i}") for i in range(threads)]
        for t in pool:
            t.start()
        for t in pool:
            t.join()
    return done


HEARTBEAT_S = 60.0
STALE_AFTER = timedelta(minutes=10)


@contextmanager
def heartbeat(factory: sessionmaker[Session], run_id: str, every: Optional[float] = None) -> Iterator[None]:
    """Touch the run's heartbeat_at while it executes, so a long audit isn't mistaken for a dead one."""
    every = HEARTBEAT_S if every is None else every
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(every):
            try:
                with factory() as s:
                    s.execute(update(m.JobRun).where(m.JobRun.id == run_id, m.JobRun.status == "running")
                              .values(heartbeat_at=utcnow()))
                    s.commit()
            except Exception:  # a missed beat only matters once STALE_AFTER passes without one
                pass

    t = threading.Thread(target=beat, name=f"wavyos-heartbeat-{run_id[:8]}", daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=5)


def requeue_stuck(session: Session, stale_after: timedelta = STALE_AFTER, max_attempts: int = 3) -> int:
    """A worker that died mid-run leaves it 'running' with a heartbeat that stops moving. Put it back,
    up to max_attempts; after that mark it failed so it doesn't sit 'running' forever."""
    cutoff = utcnow() - stale_after
    dead = (m.JobRun.status == "running") & (func.coalesce(m.JobRun.heartbeat_at, m.JobRun.started_at) < cutoff)
    res = session.execute(update(m.JobRun).where(dead, m.JobRun.attempts < max_attempts).values(status="queued"))
    session.execute(update(m.JobRun).where(dead, m.JobRun.attempts >= max_attempts).values(
        status="failed", finished_at=utcnow(), error=f"the worker stopped responding {max_attempts} times"))
    return res.rowcount
