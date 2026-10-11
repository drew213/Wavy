"""An audit run's stored data -> scorecard, measured sizing and opportunity cards.

The brand path: client.onboard starts a collector run (audit.collect); when collection finishes,
audit.analyze calls run_audit_for_run, which scores the run's artifacts and orders with the check
library and ranks opportunities from audit/playbooks.json.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from .. import models as m
from ..loop import changes, outcomes
from ..scope import InvalidState, Scope, get_scoped
from . import opportunities
from .engine import results_of, score_snapshot
from .library import specs as library_specs

SCOREABLE = ("ready", "partial")


def _numeric(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def scorecard_facts(summary: dict, rows: dict[str, dict], specs: dict[str, dict], built: dict) -> list[dict]:
    """The scorecard's numbers as ledger facts: the score, each check's values and the numbers its message
    quotes, and every sizing input and range. With these, every number a written section can see is in
    audit_facts, so the facts check can hold drafts to the ledger alone."""
    from ..narrative.numbers import tokens
    out: list[dict] = []

    def add(fid: str, value: Any, unit: str, label: str, source: str, module: str, ec: str = "documented") -> None:
        out.append({"id": fid, "value": value, "unit": unit, "label": label, "window": "", "time_basis": "event",
                    "source": source, "module": module, "evidence_class": ec})

    if _numeric(summary.get("score")):
        add("checks.score", summary["score"], "points", "Foundational score (0-100)", "scorecard#/score", "checks")
    for cid, r in sorted(rows.items()):
        title = (specs.get(cid) or {}).get("title") or cid
        for k, v in sorted((r.get("value") or {}).items()):
            if _numeric(v):
                add(f"checks.{cid}.{k}", v, "", f"{title}: {k.replace('_', ' ')}", f"scorecard#/checks/{cid}/value/{k}", "checks")
        for i, tok in enumerate(tokens(r.get("message") or ""), 1):
            add(f"checks.{cid}.m{i}", tok.value, "%" if tok.pct else "USD" if tok.currency else "",
                f"{title} (number {i} in the check's finding)", f"scorecard#/checks/{cid}/message", "checks")
    for lever, s in sorted((built.get("sizing") or {}).items()):
        label = s.get("label") or lever
        for name, part in (("pool", s.get("pool") or {}), ("value_per_order", s.get("value_per_order") or {})):
            if _numeric(part.get("value")):
                add(f"sizing.{lever}.{name}", part["value"], "USD" if name == "value_per_order" else "people",
                    f"{label}: {name.replace('_', ' ')}", f"scorecard#/sizing/{lever}/{name}", "sizing")
        for k in ("low", "base", "high"):
            if _numeric((s.get("rate") or {}).get(k)):
                add(f"sizing.{lever}.rate_{k}", s["rate"][k], "ratio", f"{label}: assumed rate ({k})",
                    f"scorecard#/sizing/{lever}/rate", "sizing", "inferred")
            if _numeric((s.get("annual") or {}).get(k)):
                add(f"sizing.{lever}.annual_{k}", s["annual"][k], "USD", f"{label}: modeled annual value ({k})",
                    f"scorecard#/sizing/{lever}/annual", "sizing", "inferred")
        if _numeric(s.get("monthly_base")):
            add(f"sizing.{lever}.monthly_base", s["monthly_base"], "USD", f"{label}: modeled monthly value (base)",
                f"scorecard#/sizing/{lever}/monthly_base", "sizing", "inferred")
    for k, v in ((built.get("total") or {}).get("annual") or {}).items():
        if _numeric(v):
            add(f"sizing.total.annual_{k}", v, "USD", f"All sized levers, each pool counted once: modeled annual ({k})",
                "scorecard#/total/annual", "sizing", "inferred")
    return out


def run_audit_for_run(session: Session, scope: Scope, run_id: str, context: Optional[dict[str, Any]] = None,
                      link_fetcher: Optional[Callable] = None) -> m.Scorecard:
    """Score one finished audit run, run the analysis modules, and attach sizing and ranked opportunities
    (check cards and analysis findings together) to the scorecard summary.

    context adds to the run's own context (client-confirmed facts such as store_platform). link_fetcher
    checks the links in live templates; without it, links are listed but not fetched.
    """
    from .. import analysis
    from ..collector.run import snapshot_for

    run = get_scoped(session, scope, m.AuditRun, run_id)
    if run.status not in SCOREABLE:
        raise InvalidState(f"audit run {run.id} is {run.status}; only ready or partial runs can be scored")
    snap = snapshot_for(session, run, context={**(run.context or {}), **(context or {})})
    sc = score_snapshot(session, scope, snap, run.track, create_insights=True, snapshot_ref=f"audit_run:{run.id}")
    rows = {cid: {"verdict": r.verdict, "message": r.message, "evidence": r.evidence or [], "value": r.value or {}}
            for cid, r in results_of(session, sc).items()}
    result = analysis.run_all(snap, fetch=link_fetcher)
    ch = changes.compare(session, run, sc, rows, result["facts"])
    built = opportunities.build(snap, rows, library_specs(), findings=result["findings"] + [f.as_dict() for f in ch.findings],
                                unavailable=result["unavailable"], record=outcomes.track_record(session, run.org_id))
    prev = getattr(ch, "prev", None)
    changes.opportunity_diff(ch, session.get(m.Scorecard, prev.scorecard_id) if prev is not None else None,
                             built["opportunities"])
    result["modules"][changes.MODULE] = changes.as_module(ch)
    result["facts"] = result["facts"] + [f.__dict__ for f in ch.facts] + scorecard_facts(sc.summary or {}, rows,
                                                                                      library_specs(), built)
    result["findings"] = result["findings"] + [f.as_dict() for f in ch.findings]
    analysis.store(session, run, result)
    sc.summary = {**(sc.summary or {}), "audit_run_id": run.id, **built,
                  "analysis": {name: {"status": v["status"], "reason": v["reason"], "facts": v["facts"],
                                      "findings": v["findings"]} for name, v in result["modules"].items()}}
    run.scorecard_id = sc.id
    session.flush()
    return sc
