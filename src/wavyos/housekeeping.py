"""Clearing out data nobody will read again, run on the worker's scheduler tick.

Raw audit artifacts (the API responses a run collected) are the bulk of the database. Only the
last KEEP_RUNS finished runs per brand keep theirs; older runs keep their row, stage history and
scorecard. Orders (audit_orders) are per brand, not per run, and are never pruned here.
"""
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import models as m
from . import ratelimit

KEEP_RUNS = 3
FINISHED = ("ready", "partial", "failed")


def prune_audit_artifacts(session: Session, keep: int = KEEP_RUNS) -> int:
    newest_first = func.row_number().over(partition_by=m.AuditRun.client_id, order_by=m.AuditRun.created_at.desc())
    ranked = select(m.AuditRun.id, newest_first.label("rank")).where(m.AuditRun.status.in_(FINISHED)).subquery()
    old = select(ranked.c.id).where(ranked.c.rank > keep)
    return session.execute(delete(m.AuditArtifact).where(m.AuditArtifact.run_id.in_(old))
                           .execution_options(synchronize_session=False)).rowcount


def run(session: Session) -> str:
    """One pass. Returns what was removed ('' if nothing) for the worker log."""
    done = {"expired rate-limit rows": ratelimit.prune(session), "old audit artifacts": prune_audit_artifacts(session)}
    return ", ".join(f"removed {n} {what}" for what, n in done.items() if n)
