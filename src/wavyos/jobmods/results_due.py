"""results.due: every finished piece of work gets measured when its window closes."""
from __future__ import annotations

from datetime import timedelta

from .. import models as m
from .. import operations
from ..jobs import JobContext, JobOutcome, job
from ._util import open_job_tasks, plural, today_for

KEY = "results.due"


def marker(result_id: str) -> str:
    """Written into the task's done_when so tomorrow's run can tell this result already has a task."""
    return f"result:{result_id}"


@job(KEY, "Measure results whose window has closed", "daily", "analyst", area="reporting",
     checks="Every pending result: has its measurement window closed?",
     action="Opens one internal task per closed window for the analyst to pull the metric, compare it with the "
            "baseline and record the result. A result that already has an open measurement task gets no second one.",
     completion="Done when every due result has exactly one open measurement task, or the run says none were due.")
def results_due(ctx: JobContext) -> JobOutcome:
    s, scope = ctx.session, ctx.scope
    today = today_for(ctx)
    due = operations.due_results(s, scope, today)
    if not due:
        return JobOutcome("checked pending results", no_action_reason="no pending result has a closed window")
    covered = " ".join(t.done_when for t in open_job_tasks(ctx, KEY))
    created = []
    for r in due:
        if marker(r.id) in covered:
            continue
        origin = s.get(m.Task, r.task_id)
        baseline = "no baseline recorded" if r.baseline is None else f"baseline {r.baseline}"
        task = operations.create_task(
            s, scope, job_run_id=ctx.run.id, area="reporting", owner_role="analyst",
            title=f"Measure {r.metric} for: {origin.title if origin else r.task_id}",
            done_when=f"The result is recorded as measured (observed value and notes) or inconclusive with the reason "
                      f"({marker(r.id)}).",
            checklist=[f"Pulled {r.metric} for {r.window_start.isoformat()} to {r.window_end.isoformat()} "
                       f"from the source of record",
                       f"Compared it with the {baseline} and noted anything else that moved in the window",
                       "Recorded the result, so the decision can close and feed the learning log"],
            due_date=today + timedelta(days=2))
        ctx.note("tasks", task.id)
        created.append(task)
    if not created:
        return JobOutcome(f"{plural(len(due), 'result')} due",
                          no_action_reason="every due result already has an open measurement task")
    return JobOutcome(f"opened {plural(len(created), 'measurement task')} for {plural(len(due), 'due result')}")
