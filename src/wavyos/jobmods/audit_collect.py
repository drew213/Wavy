"""audit.collect: read one client's Klaviyo account into an audit run (wavyos.collector).

Queued by collector.run.start. Safe to run again: a finished run is left alone, and an interrupted
one continues from its saved stage state.
"""
from __future__ import annotations

import time

from .. import connections, jobs
from .. import models as m
from ..collector import run as collector
from ..jobs import JobContext, JobOutcome, job
from ..scope import get_scoped

KEY = collector.JOB_KEY


@job(KEY, "Collect a Klaviyo audit", "event", "analyst", area="onboarding", events=(KEY,),
     checks="The audit run on the job and the client's sealed Klaviyo key.",
     action="Read the account stage by stage into audit artifacts and stored orders, resuming any stage that was interrupted.",
     completion="Done when every stage is done, failed or skipped, and the run is ready, partial or failed.")
def audit_collect(ctx: JobContext) -> JobOutcome:
    run_id = ((ctx.run.trigger or {}).get("payload") or {}).get("run_id")
    run = get_scoped(ctx.session, ctx.scope, m.AuditRun, run_id)
    if run.status not in collector.ACTIVE:
        return JobOutcome(f"audit run {run.id} is already {run.status}", no_action_reason="the run had already finished")
    vault, make_client = ctx.services.get("vault"), ctx.services.get("klaviyo_factory")
    if vault is None or make_client is None:
        raise RuntimeError("audit.collect needs the vault and a Klaviyo client factory in the worker's services")
    with connections.secret(ctx.session, ctx.scope, vault, run.connection_id, "audit collection") as key:
        client = make_client(key)
    try:
        collector.collect(ctx.session, ctx.scope, run, client, pins=collector.pins_for(ctx.session, run.client_id),
                          sleep=ctx.services.get("sleep") or time.sleep)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    ctx.note("audit_runs", run.id)
    if run.status in ("ready", "partial"):
        jobs.emit(ctx.session, ctx.scope, "audit.analyze", {"run_id": run.id}, dedupe=run.id)
    return JobOutcome(f"audit run {run.id}: {run.status}")
