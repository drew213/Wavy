"""outcomes.readout: read every approved opportunity against a newly scored audit (wavyos.loop.outcomes).

Queued by audit.analyze for each scored run. Verdicts feed the next ranking through track_record.
"""
from __future__ import annotations

from collections import Counter

from .. import workspace
from .. import models as m
from ..jobs import JobContext, JobOutcome, job
from ..loop import outcomes
from ..scope import get_scoped

KEY = "outcomes.readout"


@job(KEY, "Opportunity readouts", "event", "analyst", area="reporting", events=(KEY,),
     checks="Every approved opportunity still being tracked for this brand, against the newly scored audit.",
     action="Adds the run's value of each outcome's baseline fact; writes improved / flat / worse / unmeasured once "
            "the readout window has passed.",
     completion="Done when each tracked outcome has this run's readout or a verdict.")
def outcomes_readout(ctx: JobContext) -> JobOutcome:
    run_id = ((ctx.run.trigger or {}).get("payload") or {}).get("run_id")
    run = get_scoped(ctx.session, ctx.scope, m.AuditRun, run_id)
    touched = outcomes.read(ctx.session, ctx.scope, run)
    if not touched:
        return JobOutcome(f"audit run {run.id}: no readouts", no_action_reason="no approved opportunity is being tracked")
    for o in touched:
        ctx.note("outcomes", o.id)
    workspace.publish(ctx.session, run)
    counts = Counter(o.status for o in touched)
    return JobOutcome(f"audit run {run.id}: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
