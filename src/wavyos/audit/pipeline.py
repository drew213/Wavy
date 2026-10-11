"""An audit run's stored data -> scorecard, measured sizing and opportunity cards.

The brand path: client.onboard starts a collector run (audit.collect); when collection finishes,
audit.analyze calls run_audit_for_run, which scores the run's artifacts and orders with the check
library and ranks opportunities from audit/playbooks.json.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from .. import models as m
from ..scope import InvalidState, Scope, get_scoped
from . import opportunities
from .engine import results_of, score_snapshot
from .library import specs as library_specs

SCOREABLE = ("ready", "partial")


def run_audit_for_run(session: Session, scope: Scope, run_id: str, context: Optional[dict[str, Any]] = None) -> m.Scorecard:
    """Score one finished audit run and attach sizing and opportunities to the scorecard summary.

    context adds to the run's own context (client-confirmed facts such as store_platform).
    """
    from ..collector.run import snapshot_for

    run = get_scoped(session, scope, m.AuditRun, run_id)
    if run.status not in SCOREABLE:
        raise InvalidState(f"audit run {run.id} is {run.status}; only ready or partial runs can be scored")
    snap = snapshot_for(session, run, context={**(run.context or {}), **(context or {})})
    sc = score_snapshot(session, scope, snap, run.track, create_insights=True, snapshot_ref=f"audit_run:{run.id}")
    rows = {cid: {"verdict": r.verdict, "message": r.message, "evidence": r.evidence or [], "value": r.value or {}}
            for cid, r in results_of(session, sc).items()}
    built = opportunities.build(snap, rows, library_specs())
    sc.summary = {**(sc.summary or {}), "audit_run_id": run.id, **built}
    run.scorecard_id = sc.id
    session.flush()
    return sc
