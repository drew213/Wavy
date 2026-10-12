"""impact.measure: read what each live fix earned (wavyos.impact), daily after the overnight refresh."""
from __future__ import annotations

from sqlalchemy import select

from .. import connections, impact
from .. import models as m
from ..collector import budget
from ..connectors.klaviyo import KlaviyoError
from ..db import utcnow
from ..jobs import JobContext, JobOutcome, job

KEY = "impact.measure"
HOUR = 4


def _placed_order(session, run: m.AuditRun):
    art = session.scalar(select(m.AuditArtifact).where(m.AuditArtifact.run_id == run.id, m.AuditArtifact.name == "totals"))
    return ((((art.payload if art else {}) or {}).get("metricChoices") or {}).get("placed_order") or {}).get("used") or {}


@job(KEY, "Measure live fixes", "daily", "analyst", area="reporting", hour=HOUR, family="analysis",
     checks="Every approved fix for this brand that has been live at least 7 days, against Klaviyo's flow values "
            "before and since the change.",
     action="Writes an impact reading per fix: measured lift (before vs after, adjusted for the trend in flows it "
            "didn't touch), or attributed revenue only for flows that weren't sending before.",
     completion="Done when each fix that is due has today's reading, or the brand's reporting budget is spent.")
def impact_measure(ctx: JobContext) -> JobOutcome:
    session, client_id = ctx.session, ctx.scope.require_client()
    now = utcnow()
    due = impact.due(session, client_id, now)
    if not due:
        return JobOutcome("nothing to measure", no_action_reason="no fix has been live for 7 days yet")
    conns = connections.active(session, ctx.scope, "klaviyo")
    run = session.scalar(select(m.AuditRun).where(m.AuditRun.client_id == client_id, m.AuditRun.status.in_(("ready", "partial")))
                         .order_by(m.AuditRun.created_at.desc()).limit(1))
    metric = _placed_order(session, run) if run else {}
    if not conns or run is None or not metric.get("id"):
        return JobOutcome("can't measure yet", no_action_reason="needs an active Klaviyo key and a finished audit with a Placed Order metric")
    vault, make_client = ctx.services.get("vault"), ctx.services.get("klaviyo_factory")
    if vault is None or make_client is None:
        raise RuntimeError("impact.measure needs the vault and a Klaviyo client factory in the worker's services")
    with connections.secret(session, ctx.scope, vault, conns[0].id, "measure live fixes") as key:
        client = make_client(key)

    def read(start, end):
        budget.spend(session, run.account_id)
        return impact.read_window(client, metric["id"], start, end)

    try:
        readings = impact.measure(session, session.get(m.Client, client_id), due, read, now)
    except budget.BudgetExceeded as e:
        return JobOutcome("measurement deferred", no_action_reason=str(e))
    except KlaviyoError as e:
        return JobOutcome("measurement failed", no_action_reason=f"Klaviyo refused the report: {e}")
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    for r in readings:
        ctx.note("impact_readings", r.id)
    lift = sum(r.lift or 0 for r in readings if r.status == "measured")
    return JobOutcome(f"{len(readings)} reading(s); measured lift ${lift:,.0f}")
