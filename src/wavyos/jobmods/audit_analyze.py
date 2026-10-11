"""audit.analyze: score a collected audit run, size it and rank its opportunities (wavyos.audit.pipeline).

Queued by audit.collect when a run finishes ready or partial. Safe to run again: a run that already
has a scorecard is left alone.
"""
from __future__ import annotations

from .. import jobs, workspace
from .. import models as m
from ..audit.pipeline import SCOREABLE, run_audit_for_run
from ..jobs import JobContext, JobOutcome, job
from ..scope import get_scoped

KEY = "audit.analyze"


@job(KEY, "Analyze a collected audit", "event", "analyst", area="onboarding", events=(KEY,),
     checks="The audit run on the job, which must have finished collecting.",
     action="Score the run with the check library, size the levers from measured inputs and rank opportunity cards.",
     completion="Done when the run has a scorecard with sizing and opportunities.")
def audit_analyze(ctx: JobContext) -> JobOutcome:
    run_id = ((ctx.run.trigger or {}).get("payload") or {}).get("run_id")
    run = get_scoped(ctx.session, ctx.scope, m.AuditRun, run_id)
    if run.status not in SCOREABLE:
        return JobOutcome(f"audit run {run.id} is {run.status}", no_action_reason="only ready or partial runs are scored")
    if run.scorecard_id:
        return JobOutcome(f"audit run {run.id} is already scored", no_action_reason="the run already has a scorecard")
    sc = run_audit_for_run(ctx.session, ctx.scope, run.id, link_fetcher=ctx.services.get("link_fetcher"))
    ctx.note("scorecards", sc.id)
    ctx.note("workspace_bundles", workspace.publish(ctx.session, run).id)
    jobs.emit(ctx.session, ctx.scope, "narrative.draft", {"run_id": run.id}, dedupe=run.id)
    summary = sc.summary or {}
    unavailable = [k for k, v in (summary.get("analysis") or {}).items() if v.get("status") != "ok"]
    return JobOutcome(f"audit run {run.id} scored {summary.get('score')}: "
                      f"{len(summary.get('opportunities') or [])} opportunities"
                      + (f"; analysis unavailable: {', '.join(unavailable)}" if unavailable else ""))
