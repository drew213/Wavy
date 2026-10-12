"""narrative.draft: write, check and critique the written sections of a scored audit run (wavyos.narrative).

Queued by audit.analyze once the measured workspace is published, and by staff from the review queue to
redraft. Sections a strategist already reviewed are never overwritten.
"""
from __future__ import annotations

from .. import models as m
from ..jobs import JobContext, JobOutcome, job
from ..narrative import drafting
from ..narrative.review import republish
from ..scope import get_scoped

KEY = "narrative.draft"


@job(KEY, "Draft the written audit sections", "event", "analyst", area="onboarding", events=(KEY,), family="llm",
     checks="A scored audit run and the brand's allowed model providers.",
     action="Draft each written section from its fact packet, block numbers not in the ledger, have a second "
            "provider critique it (up to 3 rounds), and queue passing drafts for strategist approval.",
     completion="Done when every written section is queued for review, needs a rewrite, or says why it was skipped.")
def narrative_draft(ctx: JobContext) -> JobOutcome:
    payload = (ctx.run.trigger or {}).get("payload") or {}
    run = get_scoped(ctx.session, ctx.scope, m.AuditRun, payload.get("run_id"))
    if not run.scorecard_id:
        return JobOutcome(f"audit run {run.id} isn't scored", no_action_reason="only scored runs are drafted")
    factory = ctx.services.get("writers")
    writers = factory(run.org_id) if callable(factory) else (factory or {})
    rows = drafting.draft_run(ctx.session, ctx.scope, run, writers, sections=payload.get("sections"))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
        if r.status != "skipped":
            ctx.note("narrative_drafts", r.id)
        if r.approval_id:
            ctx.note("approvals", r.approval_id)
    republish(ctx.session, run.id)
    summary = (f"audit run {run.id}: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
               + f"; ${sum(r.cost_usd for r in rows):.2f} spent")
    skipped = [r.reason for r in rows if r.status == "skipped"]
    return JobOutcome(summary, no_action_reason=skipped[0] if skipped else "")
