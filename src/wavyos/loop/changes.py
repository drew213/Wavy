"""What changed since the brand's previous scored audit, as an analysis module ("changes").

Its numbers are ledger facts like any other module's (changes.<fact id>.before / .delta / .pct_change,
the score delta, checks that moved), so the monthly report's summary can quote them through the facts
check. Checks that got worse and KPIs that moved the wrong way become findings, so they rank as
opportunities. A brand's first audit has nothing to compare: the module says so instead of showing zeros.
"""
from __future__ import annotations

import re
from datetime import timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..analysis.base import Analysis, pct
from ..audit.engine import VERDICT_RANK, results_of

MODULE = "changes"
LOWER_IS_BETTER = re.compile(r"spam|unsub|bounce|complain|fail|empty|on_hold|cancel|refund|churn|silent|missing|"
                             r"error|broken|lapsed|suppress|days_to|gap", re.I)
SKIP_MODULES = {"checks", "sizing", MODULE}
LEAD_MODULES = ("kpi_scorecard", "performance", "flow_categories")
MAX_MOVERS = 40
MIN_MOVE = 0.01        # a fact that moved less than 1% isn't listed
WRONG_WAY = 0.2        # a lead metric 20% worse than last time is a finding


def lower_is_better(fact_id: str, label: str = "") -> bool:
    return bool(LOWER_IS_BETTER.search(fact_id) or LOWER_IS_BETTER.search(label or ""))


def previous_run(session: Session, run: m.AuditRun) -> Optional[m.AuditRun]:
    return session.scalar(select(m.AuditRun).where(
        m.AuditRun.client_id == run.client_id, m.AuditRun.id != run.id, m.AuditRun.scorecard_id.is_not(None),
        m.AuditRun.created_at <= run.created_at).order_by(m.AuditRun.created_at.desc()).limit(1))


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _when(dt) -> str:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).date().isoformat() if dt else ""


def compare(session: Session, run: m.AuditRun, sc: m.Scorecard, rows: dict[str, dict], facts: list[dict]) -> Analysis:
    """The changes module for `run` against the brand's previous scored run. Raises nothing: with no previous
    run the Analysis carries no facts and a note, and the caller marks the module unavailable."""
    from ..audit.library import specs as library_specs
    a = Analysis(MODULE)
    prev = previous_run(session, run)
    a.prev = prev  # type: ignore[attr-defined]
    if prev is None:
        a.notes.append("This is the brand's first scored audit; What changed fills in from the next refresh.")
        return a
    prev_sc = session.get(m.Scorecard, prev.scorecard_id)
    specs = library_specs()
    a.fact("days_between", (_aware(run.created_at) - _aware(prev.created_at)).days, "days",
           "Days since the previous audit", source=f"audit_run:{prev.id}")
    same_library = (prev_sc.library_version or "") == (sc.library_version or "")
    if not same_library:
        a.notes.append(f"The check library changed ({prev_sc.library_version} to {sc.library_version}); "
                       "check verdicts are compared, but a moved verdict may be the library, not the account.")
    bs, as_ = (prev_sc.summary or {}).get("score"), (sc.summary or {}).get("score")
    if _num(bs):
        a.fact("previous_score", bs, "points", "Foundational score at the previous audit", source=f"audit_run:{prev.id}")
    if _num(bs) and _num(as_):
        a.fact("score_delta", round(as_ - bs, 1), "points", "Foundational score change since the previous audit")

    before = {cid: r.verdict for cid, r in results_of(session, prev_sc).items()}
    moved, regressed, improved = [], [], []
    for cid in sorted(set(before) | set(rows)):
        bv, av = before.get(cid), (rows.get(cid) or {}).get("verdict")
        if bv == av:
            continue
        if bv in VERDICT_RANK and av in VERDICT_RANK:
            direction = "improved" if VERDICT_RANK[av] > VERDICT_RANK[bv] else "regressed"
        else:
            direction = "changed"
        row = {"check_id": cid, "title": (specs.get(cid) or {}).get("title") or cid, "before": bv, "after": av,
               "direction": direction}
        moved.append(row)
        (regressed if direction == "regressed" else improved if direction == "improved" else []).append(row)
    a.fact("checks_improved", len(improved), "checks", "Checks that improved since the previous audit")
    a.fact("checks_regressed", len(regressed), "checks", "Checks that got worse since the previous audit")
    a.table("checks", moved)

    prior = {f.fact_id: f for f in session.scalars(select(m.AuditFact).where(m.AuditFact.run_id == prev.id))}
    movers = []
    for f in facts:
        if f["module"] in SKIP_MODULES or not _num(f["value"]) or f["id"] not in prior:
            continue
        old = (prior[f["id"]].value or {}).get("v")
        if not _num(old) or old == f["value"]:
            continue
        change = (f["value"] - old) / abs(old) if old else None
        if change is not None and abs(change) < MIN_MOVE:
            continue
        down = lower_is_better(f["id"], f["label"])
        good = (f["value"] < old) if down else (f["value"] > old)
        movers.append({"fact": f["id"], "label": f["label"], "unit": f["unit"], "module": f["module"], "before": old,
                       "after": f["value"], "delta": f["value"] - old, "pct_change": change, "good": good})
    lead = lambda r: (r["module"] not in LEAD_MODULES, -abs(r["pct_change"] if r["pct_change"] is not None else 1))  # noqa: E731
    movers = sorted(movers, key=lead)[:MAX_MOVERS]
    for r in movers:
        base = f"{r['fact']}"
        if len(base) + 12 > 120:
            continue
        r["facts"] = {"before": a.fact(f"{base}.before", r["before"], r["unit"], f"{r['label']}, previous audit",
                                       source=f"audit_run:{prev.id}"),
                      "delta": a.fact(f"{base}.delta", r["delta"], r["unit"], f"{r['label']}, change since previous audit"),
                      "pct_change": a.fact(f"{base}.pct_change", r["pct_change"], "ratio",
                                           f"{r['label']}, % change since previous audit")}
    a.table("movers", movers)
    a.table("summary", [{"previous_run": prev.id, "previous_at": _when(prev.created_at), "this_at": _when(run.created_at),
                         "library_before": prev_sc.library_version, "library_after": sc.library_version,
                         "score_before": bs, "score_after": as_, "same_library": same_library}])

    if regressed:
        a.find("regressions", f"{len(regressed)} check(s) got worse since the last audit",
               f"Checks that passed or warned on {_when(prev.created_at)} are worse now.",
               "; ".join(f"{r['check_id']} {r['title']}: {r['before']} to {r['after']}" for r in regressed[:6]) + ".",
               "Fix these before new work: open each check's card for the steps, find what changed in the account "
               "since the last audit (a flow edit, a list change, a new integration) and restore it.",
               severity="high", category="fix_first", facts=[f"{MODULE}.checks_regressed"], effort_days=1,
               readout={"metric": "checks that got worse", "days": 14, "fact": f"{MODULE}.checks_regressed"})
    wrong = [r for r in movers if r["module"] in LEAD_MODULES and not r["good"] and r["pct_change"] is not None
             and abs(r["pct_change"]) >= WRONG_WAY and r.get("facts")]
    if wrong:
        a.find("wrong_way", "Lead metrics that moved the wrong way",
               f"{len(wrong)} lead metric(s) moved at least {pct(WRONG_WAY, 0)} the wrong way since the last audit.",
               "; ".join(f"{r['label']}: {pct(r['pct_change'])}" for r in wrong[:5]) + ".",
               "For each, confirm the move is real (enough volume, tracking intact, not a seasonal month), then name an "
               "owner and the one change that would reverse it; the metric's own section shows its history.",
               severity="high", category="measurement",
               facts=[f"{r['fact']}.pct_change" for r in wrong[:5]], effort_days=1,
               readout={"metric": wrong[0]["label"], "days": 30, "fact": wrong[0]["fact"]})
    return a


def opportunity_diff(a: Analysis, prev_sc: Optional[m.Scorecard], cards: list[dict]) -> None:
    """Which opportunity cards are new, still open or resolved since the previous run."""
    if prev_sc is None:
        return
    before = {c["id"]: c for c in (prev_sc.summary or {}).get("opportunities") or []}
    now = {c["id"]: c for c in cards}
    table = [{"id": cid, "title": c["title"], "rank": c.get("rank"), "change": "still_open" if cid in before else "new"}
             for cid, c in now.items()]
    table += [{"id": cid, "title": c["title"], "rank": None, "change": "resolved"} for cid, c in before.items() if cid not in now]
    a.fact("opportunities_new", sum(1 for r in table if r["change"] == "new"), "cards", "New opportunities since the previous audit")
    a.fact("opportunities_resolved", sum(1 for r in table if r["change"] == "resolved"), "cards",
           "Opportunities resolved since the previous audit (no longer flagged)")
    a.table("opportunities", sorted(table, key=lambda r: ({"new": 0, "resolved": 1, "still_open": 2}[r["change"]], r["rank"] or 999)))


def as_module(a: Analysis) -> dict[str, Any]:
    status = "ok" if getattr(a, "prev", None) is not None else "unavailable"
    reason = a.notes[0] if status != "ok" and a.notes else ""
    return {"status": status, "reason": reason, "tables": a.tables, "notes": a.notes, "facts": len(a.facts),
            "findings": len(a.findings)}


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
