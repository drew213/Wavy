"""The audit engine: run the check library against a saved snapshot, write a scorecard, turn every
warn and fail into an insight with its evidence.

Read-only with respect to the client's accounts: it reads saved collector files and writes only
Wavy OS rows (scorecard, check results, insights, events).
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import events
from .. import models as m
from ..db import new_id
from ..operations import create_insight
from ..policy import authorize
from ..scope import NotPermitted, Scope, get_scoped
from ..taxonomy import CHECK_AREA_MAP
from .checks import CheckOutcome, as_dict, outcome, run_check
from .library import TRACKS, applies, load_library
from .snapshot import Snapshot

SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
VERDICT_POINTS = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
WARN_SEVERITY = {"critical": "high", "high": "medium", "medium": "low", "low": "info", "info": "info"}
VERDICT_RANK = {"fail": 0, "warn": 1, "pass": 2}
_UNCOLLECTED = {"needs-collector": "needs a collector that doesn't exist yet", "manual": "manual check: needs auditor evidence"}


def score(results: dict[str, CheckOutcome], specs: dict[str, dict]) -> Optional[float]:
    """0-100 score over the checks that reached a verdict.

        score = 100 * sum(w(c) * p(c)) / sum(w(c))   for c with verdict pass, warn or fail

    w is the check's severity weight (critical 4, high 3, medium 2, low 1) and p is 1 for pass,
    0.5 for warn, 0 for fail. na, not_implemented and error are left out (they are reported as
    coverage instead), so missing data never moves the score. None when nothing was scored.
    """
    num = den = 0.0
    for cid, out in results.items():
        if out.verdict not in VERDICT_POINTS:
            continue
        w = SEVERITY_WEIGHT.get(specs[cid].get("severity", "medium"), 2)
        num += w * VERDICT_POINTS[out.verdict]
        den += w
    return round(100 * num / den, 1) if den else None


def evaluate(snap: Snapshot, track: str = "snapshot", library: Optional[dict] = None) -> dict[str, CheckOutcome]:
    """Every library check -> its outcome on this snapshot (no database involved)."""
    lib = library or load_library()
    out: dict[str, CheckOutcome] = {}
    for spec in lib["checks"]:
        cid = spec["id"]
        if not applies(spec, track):
            out[cid] = outcome("na", f"not part of the {track} track (runs in: {', '.join(spec.get('tracks') or [])})")
        elif spec.get("status") in _UNCOLLECTED:
            out[cid] = outcome("na", f"{_UNCOLLECTED[spec['status']]}: {(spec.get('source') or {}).get('file', '')}".rstrip(": "))
        else:
            out[cid] = run_check(cid, snap, spec)
    return out


def _summary(results: dict[str, CheckOutcome], specs: dict[str, dict], snap: Snapshot, track: str) -> dict[str, Any]:
    by_verdict = {v: 0 for v in m.VERDICTS}
    by_area: dict[str, dict[str, int]] = {}
    for cid, out in results.items():
        by_verdict[out.verdict] += 1
        area = specs[cid]["area"]
        by_area.setdefault(area, {v: 0 for v in m.VERDICTS})[out.verdict] += 1
    scored = sum(by_verdict[v] for v in VERDICT_POINTS)
    return {"score": score(results, specs), "by_verdict": by_verdict, "by_area": by_area, "checks": len(results),
            "scored": scored, "coverage": round(scored / len(results), 3) if results else 0.0,
            "missing_files": snap.missing(track), "present_files": snap.present(), "track": track,
            "score_formula": "100 * sum(severity weight * points) / sum(severity weight); critical 4, high 3, "
                             "medium 2, low 1; pass 1, warn 0.5, fail 0; na/not_implemented/error excluded"}


def run_audit(session: Session, scope: Scope, snapshot_dir: str, track: str = "snapshot", create_insights: bool = True,
              context: Optional[dict[str, Any]] = None) -> m.Scorecard:
    """Score one saved snapshot for the client in scope.

    context: client-confirmed facts the collectors can't know (store_platform, sells_subscriptions,
    subscription_native_messages); checks that need one and don't get it say so.
    Each warn/fail becomes an insight (area from CHECK_AREA_MAP; severity from the check, one level
    lower for a warn) deduplicated per client by `check:<id>` while it stays open.
    """
    return score_snapshot(session, scope, Snapshot(snapshot_dir, context=context), track, create_insights)


def score_snapshot(session: Session, scope: Scope, snap: Snapshot, track: str = "snapshot",
                   create_insights: bool = True, snapshot_ref: Optional[str] = None) -> m.Scorecard:
    """run_audit for a Snapshot already in hand (a folder or an audit run's stored artifacts)."""
    client_id = scope.require_client()
    auth = authorize(session, scope, "analysis.run")
    if not auth.allowed:
        raise NotPermitted(auth.reason)
    if track not in TRACKS:
        raise ValueError(f"track must be one of {TRACKS}")
    lib = load_library()
    specs = {c["id"]: c for c in lib["checks"]}
    results = evaluate(snap, track, lib)
    summary = _summary(results, specs, snap, track)

    sc = m.Scorecard(id=new_id(), org_id=scope.org_id, client_id=client_id, track=track,
                     library_version=str(lib.get("version", "")), snapshot_ref=(snapshot_ref or snap.ref_label)[:500],
                     as_of=snap.as_of,
                     summary=summary, created_by=scope.actor.ref)
    session.add(sc)
    rows: dict[str, m.CheckResult] = {}
    for cid, out in results.items():
        row = m.CheckResult(scorecard=sc, check_id=cid, verdict=out.verdict, value=as_dict(out.value),
                            evidence=out.evidence, message=out.message)
        session.add(row)
        rows[cid] = row
    session.flush()

    created = reused = 0
    if create_insights:
        for cid, out in results.items():
            if out.verdict not in ("warn", "fail"):
                continue
            spec = specs[cid]
            sev = spec.get("severity", "medium")
            severity = sev if out.verdict == "fail" else WARN_SEVERITY.get(sev, "low")
            source_ref = f"scorecard:{sc.id}#{cid}"
            evidence = list(out.evidence) or [{"kind": "check", "ref": source_ref, "detail": out.message}]
            ins, new = create_insight(
                session, scope, area=CHECK_AREA_MAP[spec["area"]], title=f"{cid} {spec['title']} ({out.verdict})"[:300],
                evidence=evidence, summary=out.message, severity=severity, source_kind="check",
                source_ref=source_ref, dedupe_key=f"check:{cid}")
            rows[cid].insight_id = ins.id
            created += new
            reused += not new
        session.flush()
    summary = dict(summary, insights_created=created, insights_reused=reused)
    sc.summary = summary
    events.record(session, scope, "audit.scored", sc,
                  f"{track} audit scored {summary['score']} ({summary['scored']} of {summary['checks']} checks scored)",
                  {"score": summary["score"], "by_verdict": summary["by_verdict"], "library_version": sc.library_version,
                   "insights_created": created, "insights_reused": reused})
    return sc


def results_of(session: Session, scorecard: m.Scorecard) -> dict[str, m.CheckResult]:
    rows = session.scalars(select(m.CheckResult).where(m.CheckResult.scorecard_id == scorecard.id))
    return {r.check_id: r for r in rows}


def _numeric(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def compare(session: Session, scope: Scope, before_id: str, after_id: str) -> dict[str, Any]:
    """Per-check verdict and value deltas between two scorecards of the same client (re-audits).

    direction is improved/regressed/same by fail < warn < pass, or 'changed'/'unscored' when a side
    has na, not_implemented or error. value_delta lists after - before for numeric top-level values.
    """
    scope.require_client()
    auth = authorize(session, scope, "data.read")
    if not auth.allowed:
        raise NotPermitted(auth.reason)
    before = get_scoped(session, scope, m.Scorecard, before_id)
    after = get_scoped(session, scope, m.Scorecard, after_id)
    b_rows, a_rows = results_of(session, before), results_of(session, after)
    checks: dict[str, dict[str, Any]] = {}
    tally = {"improved": 0, "regressed": 0, "same": 0, "changed": 0, "unscored": 0}
    for cid in sorted(set(b_rows) | set(a_rows)):
        b, a = b_rows.get(cid), a_rows.get(cid)
        bv, av = (b.verdict if b else None), (a.verdict if a else None)
        if bv == av:
            direction = "same"
        elif bv in VERDICT_RANK and av in VERDICT_RANK:
            direction = "improved" if VERDICT_RANK[av] > VERDICT_RANK[bv] else "regressed"
        elif av in VERDICT_RANK or bv in VERDICT_RANK:
            direction = "changed"
        else:
            direction = "unscored"
        tally[direction] += 1
        bval, aval = (b.value if b else {}) or {}, (a.value if a else {}) or {}
        delta = {k: round(aval[k] - bval[k], 6) for k in set(bval) & set(aval) if _numeric(aval[k]) and _numeric(bval[k])
                 and aval[k] != bval[k]}
        checks[cid] = {"before": bv, "after": av, "direction": direction, "value_delta": delta}
    bs, as_ = (before.summary or {}).get("score"), (after.summary or {}).get("score")
    return {"before": before.id, "after": after.id,
            "as_of": {"before": before.as_of.isoformat() if before.as_of else None,
                      "after": after.as_of.isoformat() if after.as_of else None},
            "score": {"before": bs, "after": as_, "delta": None if bs is None or as_ is None else round(as_ - bs, 1)},
            "tally": tally, "checks": checks}
