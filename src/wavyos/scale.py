"""When to move hosting up a step (docs/SCALING.md), from what the database can see.

Step 1 (paid web + separate worker + paid database) is due when the free setup starts costing a brand:
  - a brand is marked active (paying), so the overnight batch must run while nobody has a page open;
  - storage is past WARN_SHARE of the free database;
  - due jobs have waited QUEUE_LAG or longer (the service slept or one worker can't keep up);
  - an overnight run is still queued MISSED_OVERNIGHT after its due time.
Step 2 (object storage, more workers, pooling) is due past STEP2_BRANDS brands or STEP2_SHARE of 10 GB.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models as m
from . import dbsize
from .db import utcnow
from .jobs import REGISTRY

QUEUE_LAG = timedelta(minutes=30)
MISSED_OVERNIGHT = timedelta(hours=2)
STEP2_BRANDS = 200
PAID_DB_MB = 10 * 1024
STEP2_SHARE = 0.7


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def signals(session: Session, report: dict, *, embedded_worker: Optional[bool], now: Optional[datetime] = None) -> dict:
    """{move, step, reasons, action, metrics}. embedded_worker: True when the worker runs inside the web
    service (free hosting), False when it's a separate service, None when unknown."""
    now = now or utcnow()
    by_status = dict(session.execute(select(m.Client.status, func.count()).group_by(m.Client.status)).all())
    active = by_status.get("active", 0)
    brands = sum(n for s, n in by_status.items() if s != "offboarded")
    connected = session.scalar(select(func.count(func.distinct(m.Connection.client_id))).where(
        m.Connection.provider == "klaviyo", m.Connection.status == "active")) or 0
    oldest = _aware(session.scalar(select(func.min(m.JobRun.due_at)).where(
        m.JobRun.status == "queued", m.JobRun.due_at <= now)))
    lag = now - oldest if oldest is not None else timedelta(0)
    held = [k for k, d in REGISTRY.items() if d.hour is not None]
    missed = 0
    if held:
        missed = session.scalar(select(func.count()).select_from(m.JobRun).where(
            m.JobRun.status == "queued", m.JobRun.job_key.in_(held), m.JobRun.due_at <= now - MISSED_OVERNIGHT)) or 0
    db_mb = report.get("database_mb") or 0
    share = report.get("used_share") or 0
    metrics = {"brands": brands, "active_brands": active, "connected_brands": connected, "database_mb": db_mb,
               "free_plan_share": share, "queue_lag_minutes": int(lag.total_seconds() // 60),
               "missed_overnight_runs": missed, "embedded_worker": embedded_worker}

    step1 = []
    if active:
        step1.append(f"{active} brand(s) are marked active; on free hosting the service sleeps, so the overnight "
                     "refresh, re-audit and drafting don't run until someone opens a page.")
    if share >= dbsize.WARN_SHARE:
        step1.append(f"the database is at {share:.0%} of the free plan's {report.get('free_plan_mb')} MB; writes "
                     "fail when it's full.")
    if lag >= QUEUE_LAG:
        step1.append(f"due jobs have waited {metrics['queue_lag_minutes']} minutes for a worker.")
    if missed:
        step1.append(f"{missed} overnight run(s) are still queued more than {int(MISSED_OVERNIGHT.total_seconds() // 3600)} "
                     "hours after they were due.")
    step2 = []
    if brands >= STEP2_BRANDS:
        step2.append(f"{brands} brands (Step 2 starts around {STEP2_BRANDS}).")
    if db_mb >= STEP2_SHARE * PAID_DB_MB:
        step2.append(f"the database is {db_mb:,.0f} MB, {db_mb / PAID_DB_MB:.0%} of a 10 GB paid plan.")

    if step2:
        return {"move": True, "step": "step2", "reasons": step2 + step1, "metrics": metrics,
                "action": "Move raw artifacts to object storage, add worker threads then a second worker, and use "
                          "pooled connections (docs/SCALING.md, Step 2)."}
    if step1 and embedded_worker is not False:
        return {"move": True, "step": "step1", "reasons": step1, "metrics": metrics,
                "action": "Move to paid hosting: an always-on web service, a separate background worker "
                          "(`wavyos work --loop --threads 4`) and a paid database (docs/SCALING.md, Step 1)."}
    if lag >= QUEUE_LAG or missed:
        return {"move": True, "step": "threads", "reasons": step1, "metrics": metrics,
                "action": "The separate worker is falling behind: raise --threads, then add a second worker "
                          "(docs/SCALING.md, Step 2)."}
    return {"move": False, "step": None, "reasons": [], "metrics": metrics,
            "action": "Nothing to change; the current hosting carries the load."}
