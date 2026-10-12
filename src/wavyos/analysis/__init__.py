"""The analysis layer: what an audit measured beyond pass/fail checks, as facts, findings and tables.

run_all runs every module over one Snapshot. A module whose inputs are missing is reported unavailable
with the reason (never silently dropped, never shown as zero); a module that crashes is reported as an
error and the others still run.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import models as m
from ..audit.snapshot import MissingData, Snapshot
from . import (capture_reach, cohorts, complaints, flow_categories, flow_history, journey_graph, journey_map, kpi_scorecard,
               messages, performance, program, promotions, subscriptions)
from .base import Analysis

log = logging.getLogger(__name__)

MODULES = (kpi_scorecard, performance, cohorts, flow_history, flow_categories, complaints, journey_graph, program,
           promotions, capture_reach, subscriptions, messages, journey_map)
ARTIFACT = "analysis"
LABEL_MAX = 300


def run_all(snap: Snapshot, context: Optional[dict[str, Any]] = None, fetch: Optional[Callable] = None) -> dict[str, Any]:
    """{modules: {name: {status, reason, tables, notes}}, facts: [...], findings: [...], unavailable: {name: reason}}."""
    context = {**snap.context, **(context or {})}
    out: dict[str, Any] = {"modules": {}, "facts": [], "findings": [], "unavailable": {},
                           "generated_at": datetime.now(timezone.utc).isoformat()}
    for mod in MODULES:
        a = Analysis(mod.MODULE)
        status, reason = "ok", ""
        try:
            if mod is messages:
                mod.run(snap, a, context, fetch=fetch)
            else:
                mod.run(snap, a, context)
        except MissingData as e:
            status, reason = "unavailable", e.reason
        except Exception as e:  # one module's bug must not hide the others' findings
            log.exception("analysis module %s failed", mod.MODULE)
            status, reason = "error", f"{type(e).__name__}: {e}"
        if status != "ok":
            out["unavailable"][mod.MODULE] = reason
        out["modules"][mod.MODULE] = {"status": status, "reason": reason, "tables": a.tables, "notes": a.notes,
                                      "facts": len(a.facts), "findings": len(a.findings)}
        out["facts"] += [f.__dict__ for f in a.facts]
        out["findings"] += [f.as_dict() for f in a.findings]
    return out


def _clean(node: Any) -> Any:
    """NaN and infinity aren't JSON (Postgres rejects them): stored as None."""
    if isinstance(node, float):
        return node if math.isfinite(node) else None
    if isinstance(node, dict):
        return {k: _clean(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_clean(v) for v in node]
    return node


def store(session: Session, run: m.AuditRun, result: dict[str, Any]) -> None:
    """Replace the run's fact ledger and save the full analysis (tables included) as an artifact."""
    result = _clean(json.loads(json.dumps(result, default=str)))
    session.execute(delete(m.AuditFact).where(m.AuditFact.run_id == run.id))
    for f in {f["id"]: f for f in result["facts"]}.values():
        session.add(m.AuditFact(org_id=run.org_id, client_id=run.client_id, run_id=run.id, fact_id=f["id"][:120],
                                module=f["module"], label=(f["label"] or "")[:LABEL_MAX], value={"v": f["value"]},
                                unit=f["unit"] or "", window=(f["window"] or "")[:60], time_basis=f["time_basis"],
                                source=(f["source"] or "")[:300], evidence_class=f["evidence_class"]))
    art = session.scalar(select(m.AuditArtifact).where(m.AuditArtifact.run_id == run.id, m.AuditArtifact.name == ARTIFACT))
    if art is None:
        art = m.AuditArtifact(org_id=run.org_id, client_id=run.client_id, run_id=run.id, name=ARTIFACT)
        session.add(art)
    art.payload = result
    art.captured_at = datetime.now(timezone.utc)
    session.flush()


def stored(session: Session, run: Optional[m.AuditRun]) -> Optional[dict[str, Any]]:
    """A run's saved analysis result, or None."""
    if run is None:
        return None
    art = session.scalar(select(m.AuditArtifact).where(m.AuditArtifact.run_id == run.id, m.AuditArtifact.name == ARTIFACT))
    return art.payload if art is not None else None


def facts_for(session: Session, run_id: str) -> dict[str, Any]:
    """{fact_id: value} for one run."""
    return {f.fact_id: (f.value or {}).get("v") for f in session.scalars(select(m.AuditFact).where(m.AuditFact.run_id == run_id))}
