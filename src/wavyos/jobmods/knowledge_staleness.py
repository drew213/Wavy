"""knowledge.staleness: keep the client's approved knowledge current."""
from __future__ import annotations

from datetime import timedelta

from .. import knowledge, operations
from ..jobs import JobContext, JobOutcome, job
from ._util import open_job_tasks, plural, today_for

KEY = "knowledge.staleness"


def _line(f) -> str:
    when = f"review date {f.review_by.isoformat()} has passed" if f.review_by else "no review date was ever set"
    return (f"{f.title} [{f.key}, {f.area}]: {when}. Confirm it with its owner and set a new review date, "
            f"propose a corrected fact, or retire it.")


@job(KEY, "Knowledge staleness review", "weekly", "account_lead", area="knowledge_maintenance",
     checks="Every approved client fact: is its review date in the past, or was it never given one?",
     action="Opens one review task for the account lead listing every stale fact as a checklist item. "
            "If last week's review task is still open, it waits for that one instead of piling up another.",
     completion="Done when the run has either opened the review task or said why none was needed "
                "(nothing stale, or the earlier review task is still open).")
def staleness(ctx: JobContext) -> JobOutcome:
    s, scope = ctx.session, ctx.scope
    today = today_for(ctx)
    approved = knowledge.facts(s, scope)
    stale = [f for f in approved if f.review_by is None or f.review_by < today]
    if not stale:
        return JobOutcome(f"checked {plural(len(approved), 'approved fact')}",
                          no_action_reason="no approved fact is past its review date")
    still_open = open_job_tasks(ctx, KEY)
    if still_open:
        t = still_open[0]
        return JobOutcome(f"{plural(len(stale), 'stale fact')} found",
                          no_action_reason=f"review task {t.id} from an earlier run is still open; finish it first")
    task = operations.create_task(
        s, scope, job_run_id=ctx.run.id, area="knowledge_maintenance", owner_role="account_lead",
        title=f"Review {plural(len(stale), 'stale client fact')}",
        done_when="Every fact listed is re-verified with a new review date, replaced by an approved correction, "
                  "or retired, and QA has ticked it off.",
        checklist=[_line(f) for f in stale], due_date=today + timedelta(days=7))
    ctx.note("tasks", task.id)
    return JobOutcome(f"{plural(len(stale), 'stale fact')} of {len(approved)} listed in review task {task.id}")
