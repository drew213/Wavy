"""Small helpers shared by the job handlers."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .. import models as m
from ..jobs import JobContext

CLOSED_TASK = ("done", "cancelled")


def today_for(ctx: JobContext) -> date:
    """services["today"] (a date, or a callable returning one) wins; otherwise today in the client's timezone."""
    override = ctx.services.get("today")
    if override is not None:
        value = override() if callable(override) else override
        return value.date() if isinstance(value, datetime) else value
    client = ctx.session.get(m.Client, ctx.scope.require_client())
    return datetime.now(ZoneInfo(client.timezone or "UTC")).date()


def open_job_tasks(ctx: JobContext, job_key: str) -> list[m.Task]:
    """Tasks this client still has open that an earlier run of `job_key` created."""
    q = (select(m.Task).join(m.JobRun, m.Task.job_run_id == m.JobRun.id)
         .where(m.Task.org_id == ctx.scope.org_id, m.Task.client_id == ctx.scope.require_client(),
                m.JobRun.job_key == job_key, m.Task.status.not_in(CLOSED_TASK))
         .order_by(m.Task.created_at))
    return list(ctx.session.scalars(q))


def plural(n: int, word: str, many: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"
