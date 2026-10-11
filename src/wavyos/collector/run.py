"""Start an audit run, drive it through the collector stages, and hand its data to the check engine.

A run is resumable: every stage records its status in audit_runs.stages and commits as it goes. If the
worker dies mid-run, jobs.requeue_stuck queues the job again and collect() skips finished stages and
continues the interrupted one from its checkpoint. A failed stage doesn't stop the others; stages that
needed it are skipped with the reason, and the run ends `partial`.
"""
from __future__ import annotations

import copy
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import events, jobs
from .. import models as m
from ..audit.snapshot import Snapshot
from ..connectors.klaviyo import KlaviyoError
from ..scope import InvalidState, Scope
from . import budget
from . import stages as st
from .metrics import PinError

JOB_KEY = "audit.collect"

# (key, label, function, stages it needs, the reference collector step it replaces)
STAGES: tuple[tuple[str, str, Callable[[st.Collect], dict], tuple[str, ...], str], ...] = (
    ("verify", "Connect to Klaviyo", st.verify, (), ""),
    ("first_look", "First look", st.first_look, ("verify",), ""),
    ("structure", "Read flows, forms, lists and segments", st.structure, ("first_look",), "24-fetch-account-audit-api.js"),
    ("performance", "Measure flow and campaign performance", st.performance, ("first_look",), "34-fetch-performance-cross-review.js"),
    ("templates", "Measure live email templates", st.templates, ("structure",), "37-audit-live-template-weight.js"),
    ("totals", "Read 24 months of account totals and campaigns", st.totals, ("first_look",), "38-fetch-account-totals.js"),
    ("orders", "Read 24 months of orders", st.orders, ("first_look",), "38-fetch-account-totals.js"),
    ("groups", "Count engagement segments", st.groups, ("structure",), ""),
)
LABELS = {key: label for key, label, *_ in STAGES}
ACTIVE = ("queued", "collecting")
EXPECTED_FAILURES = (KlaviyoError, st.StageFailed, PinError, budget.BudgetExceeded)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start(session: Session, scope: Scope, connection: m.Connection, *, started_by: str,
          track: str = "snapshot", now: Optional[datetime] = None) -> m.AuditRun:
    """Create a run and queue its collection job. A client already being audited gets its active run back."""
    client_id = scope.require_client()
    if connection.client_id != client_id or connection.provider != "klaviyo" or connection.status != "active":
        raise InvalidState("audits need this client's active Klaviyo connection")
    active = session.scalar(select(m.AuditRun).where(m.AuditRun.client_id == client_id, m.AuditRun.status.in_(ACTIVE))
                            .order_by(m.AuditRun.created_at.desc()).limit(1))
    if active is not None:
        return active
    spent = budget.used(session, connection.external_account_id, now)
    if spent + budget.SNAPSHOT_REPORT_CALLS > budget.REPORT_DAILY_LIMIT:
        raise budget.BudgetExceeded(f"this Klaviyo account has used {spent} of today's {budget.REPORT_DAILY_LIMIT} "
                                    "reporting calls; start the audit after midnight UTC")
    run = m.AuditRun(org_id=scope.org_id, client_id=client_id, connection_id=connection.id,
                     account_id=connection.external_account_id, track=track, started_by=started_by,
                     stages={key: {"status": "pending", "label": label} for key, label in LABELS.items()})
    session.add(run)
    events.record(session, scope, "audit.started", run, f"{track} audit queued")
    jobs.emit(session, scope, JOB_KEY, {"run_id": run.id}, dedupe=run.id)
    return run


def _set(run: m.AuditRun, key: str, **values: Any) -> None:
    stages = copy.deepcopy(run.stages or {})
    stages.setdefault(key, {}).update(values)
    run.stages = stages
    flag_modified(run, "stages")


def collect(session: Session, scope: Scope, run: m.AuditRun, client, *, pins: Optional[dict] = None,
            now: Optional[datetime] = None, sleep: Callable[[float], None] = time.sleep,
            report_limit: int = budget.REPORT_DAILY_LIMIT) -> m.AuditRun:
    """Run every stage that hasn't finished. Commits after each stage, so progress survives a crash."""
    run.status = "collecting"
    run.started_at = run.started_at or _now()
    session.commit()
    ctx = st.Collect(session, scope, run, client, now or run.started_at, pins or {}, sleep, report_limit)
    if ctx.now.tzinfo is None:
        ctx.now = ctx.now.replace(tzinfo=timezone.utc)
    for key, label, fn, needs, _ in STAGES:
        state = (run.stages or {}).get(key) or {}
        if state.get("status") in ("done", "failed", "skipped"):
            continue
        blocked = [n for n in needs if ((run.stages or {}).get(n) or {}).get("status") != "done"]
        if blocked:
            _set(run, key, status="skipped", detail=f"needs: {', '.join(LABELS[b] for b in blocked)}")
            session.commit()
            continue
        _set(run, key, status="running", started_at=state.get("started_at") or _now().isoformat())
        session.commit()
        ctx.stage = key
        try:
            out = fn(ctx) or {}
            _set(run, key, status="done", finished_at=_now().isoformat(), detail=out.pop("detail", ""), **out)
        except EXPECTED_FAILURES as e:
            session.rollback()
            _set(run, key, status="failed", finished_at=_now().isoformat(), error=str(e)[:1000])
        except Exception as e:  # a bug: record it on the stage, keep the other stages going
            session.rollback()
            _set(run, key, status="failed", finished_at=_now().isoformat(), error=f"{type(e).__name__}: {e}"[:1000])
        session.commit()
    return finish(session, scope, run, ctx)


def finish(session: Session, scope: Scope, run: m.AuditRun, ctx: st.Collect) -> m.AuditRun:
    stages = run.stages or {}
    history = {"ranAt": _now().isoformat(), "steps": [
        {"file": step, "stage": key, "ok": (stages.get(key) or {}).get("status") == "done"}
        for key, _, _, _, step in STAGES if step]}
    if (stages.get("verify") or {}).get("status") == "done":
        ctx.stage = "verify"
        payload = ctx.artifact("run")
        payload["history"] = (payload.get("history") or []) + [history]
        ctx.save("run", payload)
    statuses = {k: (v or {}).get("status") for k, v in stages.items()}
    if statuses.get("verify") != "done":
        run.status, run.error = "failed", (stages.get("verify") or {}).get("error") or "couldn't open the Klaviyo account"
    elif all(s == "done" for s in statuses.values()):
        run.status = "ready"
    else:
        run.status = "partial"
    run.finished_at = _now()
    problems = {k: v.get("error") or v.get("detail") for k, v in stages.items() if v.get("status") != "done"}
    events.record(session, scope, f"audit.{run.status}", run, f"audit collection {run.status}",
                  {"problems": problems} if problems else None)
    session.commit()
    return run


def pins_for(session: Session, client_id: str) -> dict[str, dict]:
    return {p.name: {"id": p.metric_id, "integration": p.integration}
            for p in session.scalars(select(m.MetricPin).where(m.MetricPin.client_id == client_id))}


def snapshot_for(session: Session, run: m.AuditRun, context: Optional[dict] = None) -> Snapshot:
    """The run's data as the check engine's Snapshot (the same files the Node collector wrote)."""
    files = {a.name: a.payload for a in session.scalars(select(m.AuditArtifact).where(m.AuditArtifact.run_id == run.id))}
    orders = st.orders_file(session, run)
    if orders is not None:
        files["orders"] = orders
    return Snapshot.from_dicts(files, context=context)
