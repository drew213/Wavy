"""The audit loop: a weekly incremental refresh and a monthly full re-audit, both in the overnight batch.

Both start an ordinary collector run on the same check library, so every refresh is scored, compared
with the run before it ("What changed"), drafted and queued for the morning review like the first audit.
The weekly run reads only orders placed since the last run (collector._resume_point); the monthly run
re-reads the full 24-month window and produces the month's report.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .. import connections
from .. import models as m
from ..collector import budget
from ..collector import run as collector
from ..jobs import JobContext, JobOutcome, job

WEEKLY_KEY = "audit.refresh"
MONTHLY_KEY = "monthly.report"
OVERNIGHT_HOUR = 2
WEEKLY_GAP = timedelta(days=5)   # a run this recent (the monthly re-audit, a manual one) makes the weekly redundant
CARRIED = ("store_platform", "kpis", "regulated_category", "avoid_claims", "sms_platform", "sells_subscriptions")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _start(ctx: JobContext, refresh: str, gap: timedelta) -> JobOutcome:
    session, client_id = ctx.session, ctx.scope.require_client()
    conns = connections.active(session, ctx.scope, "klaviyo")
    if not conns:
        return JobOutcome("no refresh", no_action_reason="this brand has no active Klaviyo connection")
    last = session.scalar(select(m.AuditRun).where(m.AuditRun.client_id == client_id)
                          .order_by(m.AuditRun.created_at.desc(), m.AuditRun.id.desc()).limit(1))
    if last is None:
        return JobOutcome("no refresh", no_action_reason="the first audit hasn't run yet; onboarding starts it")
    if last.status in collector.ACTIVE:
        return JobOutcome(f"audit run {last.id} is {last.status}", no_action_reason="an audit is already collecting")
    now = datetime.now(timezone.utc)
    age = now - _aware(last.created_at)
    if age < gap:
        return JobOutcome(f"audit run {last.id} started {age.days} day(s) ago",
                          no_action_reason=f"an audit ran in the last {gap.days} days")
    context = {k: v for k, v in (last.context or {}).items() if k in CARRIED}
    try:
        run = collector.start(session, ctx.scope, conns[0], started_by=f"schedule:{ctx.run.job_key}",
                              context={**context, "refresh": refresh})
    except budget.BudgetExceeded as e:
        return JobOutcome("refresh deferred", no_action_reason=str(e))
    ctx.note("audit_runs", run.id)
    return JobOutcome(f"{'full re-audit' if refresh == 'full' else 'incremental refresh'} queued as audit run {run.id}")


@job(WEEKLY_KEY, "Weekly audit refresh", "weekly", "analyst", area="reporting", hour=OVERNIGHT_HOUR,
     checks="The brand's Klaviyo account since the last audit: new orders, flow and campaign performance, "
            "deliverability and every check in the library.",
     action="Collects an incremental snapshot overnight; scoring, What changed, outcome readouts and drafts "
            "follow on their own.",
     completion="Done when a refresh run is queued, or it says why none was needed.")
def weekly_refresh(ctx: JobContext) -> JobOutcome:
    return _start(ctx, "incremental", WEEKLY_GAP)


@job(MONTHLY_KEY, "Monthly report", "monthly", "analyst", area="reporting", hour=OVERNIGHT_HOUR,
     checks="The whole account again, re-reading the full 24-month order window, on the same check library.",
     action="Re-audits overnight; the run's workspace version carries the monthly report (KPI scorecard, flow "
            "categories, What changed) and the narrative engine drafts its executive summary.",
     completion="Done when the re-audit is queued; the report reaches the brand only after a strategist approves "
                "its summary.")
def monthly_report(ctx: JobContext) -> JobOutcome:
    return _start(ctx, "full", timedelta(days=1))
