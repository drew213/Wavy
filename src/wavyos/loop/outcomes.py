"""Approved opportunities and their readouts (opportunity_outcomes).

approve() records the card's baseline: the value of its readout fact in the run it was approved from
(the fact named in the card's readout, else the first measured fact in its evidence, else the check's
own value). read() runs after every later scored run: it adds a readout, and once the readout window
has passed it writes the verdict. track_record() sums verdicts per playbook (check id or finding id)
across the organization; opportunities.build uses it to rank playbooks that have worked above ones
that haven't.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import events, workspace
from .. import models as m
from ..audit.engine import VERDICT_RANK, results_of
from ..policy import CLIENT_DECIDERS, LEADS
from ..scope import InvalidState, NotFound, NotPermitted, Scope
from .changes import lower_is_better

APPROVERS = LEADS | {"strategist"}
FLAT = 0.05                    # a move under 5% either way is flat
GRACE = timedelta(days=14)     # past due this long with no later audit: unmeasured
DECIDED = ("improved", "flat", "worse", "unmeasured")


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def source_key(card: dict) -> str:
    return card.get("check_id") or card.get("finding_id") or card["id"]


def baseline_fact(card: dict, facts: dict[str, m.AuditFact]) -> Optional[str]:
    """The fact a card's outcome is read from, or None when nothing measured maps to it."""
    def ok(fid: Optional[str]) -> bool:
        return bool(fid) and fid in facts and _num((facts[fid].value or {}).get("v"))

    readout = card.get("readout") or {}
    if ok(readout.get("fact")):
        return readout["fact"]
    for e in card.get("evidence") or []:
        ref = e.get("ref") if isinstance(e, dict) else None
        if ok(ref):
            return ref
    if card.get("check_id"):
        prefix = f"checks.{card['check_id']}."
        keys = sorted(k for k in facts if k.startswith(prefix))
        for k in [k for k in keys if not k[len(prefix):].startswith("m")] + keys:
            if ok(k):
                return k
    return None


def latest_scored(session: Session, client_id: str) -> Optional[m.AuditRun]:
    return session.scalar(select(m.AuditRun).where(m.AuditRun.client_id == client_id, m.AuditRun.scorecard_id.is_not(None))
                          .order_by(m.AuditRun.created_at.desc()).limit(1))


def view(o: m.OpportunityOutcome) -> dict:
    iso = lambda d: _aware(d).isoformat() if d else None  # noqa: E731
    return {"id": o.id, "opportunity_id": o.opportunity_id, "source_key": o.source_key, "title": o.title,
            "owner": o.owner, "approved_by": o.approved_by, "approved_at": iso(o.approved_at), "run_id": o.run_id,
            "readout_metric": o.readout_metric, "readout_fact": o.readout_fact, "direction": o.direction,
            "baseline": o.baseline, "readout_days": o.readout_days, "due_at": iso(o.due_at), "status": o.status,
            "readouts": o.readouts or [], "verdict_reason": o.verdict_reason, "decided_at": iso(o.decided_at),
            "note": o.note, "targets": o.targets or [], "shipped_at": iso(o.shipped_at), "shipped_by": o.shipped_by}


def for_client(session: Session, client_id: str) -> list[m.OpportunityOutcome]:
    return list(session.scalars(select(m.OpportunityOutcome).where(m.OpportunityOutcome.client_id == client_id)
                                .order_by(m.OpportunityOutcome.approved_at.desc())))


def approve(session: Session, scope: Scope, opportunity_id: str, *, owner: str, fact: Optional[str] = None,
            note: str = "", now: Optional[datetime] = None) -> m.OpportunityOutcome:
    """A strategist, or the brand's own decider, approves one card of the brand's latest scored audit, with an owner."""
    if not scope.actor.is_human or not (scope.actor.roles & (APPROVERS | CLIENT_DECIDERS)):
        raise NotPermitted("approving an opportunity needs one of: " + ", ".join(sorted(APPROVERS | CLIENT_DECIDERS)))
    if not owner.strip():
        raise InvalidState("an approved opportunity needs an owner")
    client_id = scope.require_client()
    run = latest_scored(session, client_id)
    if run is None:
        raise InvalidState("this brand has no scored audit yet")
    sc = session.get(m.Scorecard, run.scorecard_id)
    card = next((c for c in (sc.summary or {}).get("opportunities") or [] if c["id"] == opportunity_id), None)
    if card is None:
        raise NotFound(f"opportunity {opportunity_id} isn't in this brand's latest audit")
    open_ = session.scalar(select(m.OpportunityOutcome).where(
        m.OpportunityOutcome.client_id == client_id, m.OpportunityOutcome.opportunity_id == opportunity_id,
        m.OpportunityOutcome.status == "tracking"))
    if open_ is not None:
        raise InvalidState(f"{card['title']} is already approved and being tracked (owner {open_.owner})")
    facts = {f.fact_id: f for f in session.scalars(select(m.AuditFact).where(m.AuditFact.run_id == run.id))}
    if fact and fact not in facts:
        raise InvalidState(f"{fact} isn't a fact in this audit")
    fid = fact or baseline_fact(card, facts)
    base: dict[str, Any] = {"check": card.get("verdict") if card.get("check_id") else None}
    if fid:
        f = facts[fid]
        base.update(v=(f.value or {}).get("v"), unit=f.unit, label=f.label)
    now = now or datetime.now(timezone.utc)
    readout = card.get("readout") or {}
    days = int(readout.get("days") or 45)
    from .. import impact
    from ..analysis import stored
    tables = {k: v.get("tables") or {} for k, v in ((stored(session, run) or {}).get("modules") or {}).items()}
    row = m.OpportunityOutcome(
        org_id=run.org_id, client_id=client_id, run_id=run.id, opportunity_id=opportunity_id,
        source_key=source_key(card)[:160], title=(card.get("title") or "")[:300], owner=owner.strip()[:120],
        approved_by=scope.actor.ref[:120], approved_at=now,
        readout_metric=str(readout.get("metric") or card.get("title") or "")[:200], readout_fact=fid,
        direction="down" if fid and lower_is_better(fid, base.get("label", "")) else "up", baseline=base,
        readout_days=days, due_at=now + timedelta(days=days), status="tracking", readouts=[], note=note or "",
        updated_at=now, targets=impact.targets_for(card, tables))
    session.add(row)
    session.flush()
    events.record(session, scope, "opportunity.approved", row,
                  f"{row.title} approved; owner {row.owner}; read out after {days} days",
                  {"opportunity_id": opportunity_id, "baseline_fact": fid})
    workspace.publish(session, run)
    return row


def withdraw(session: Session, scope: Scope, outcome_id: str, note: str = "") -> m.OpportunityOutcome:
    if not scope.actor.is_human or not (scope.actor.roles & APPROVERS):
        raise NotPermitted("withdrawing an opportunity needs one of: " + ", ".join(sorted(APPROVERS)))
    row = session.get(m.OpportunityOutcome, outcome_id)
    if row is None or row.client_id != scope.require_client():
        raise NotFound("no such outcome for this brand")
    if row.status != "tracking":
        raise InvalidState(f"this outcome is already {row.status}")
    row.status, row.verdict_reason, row.decided_at = "withdrawn", note or "withdrawn by a strategist", datetime.now(timezone.utc)
    row.updated_at = row.decided_at
    events.record(session, scope, "opportunity.withdrawn", row, f"{row.title} withdrawn")
    return row


def _verdict(o: m.OpportunityOutcome) -> tuple[str, str]:
    reads = [r for r in o.readouts or []]
    last = reads[-1] if reads else None
    base = o.baseline or {}
    if last and o.source_key in (last.get("checks") or {}) and last["checks"][o.source_key] == "pass" \
            and base.get("check") in ("warn", "fail"):
        return "improved", f"{o.source_key} passes now (it was {base['check']} when approved)"
    if last and _num(base.get("v")) and _num(last.get("v")):
        b, a = base["v"], last["v"]
        change = (a - b) / abs(b) if b else (0.0 if a == b else (1.0 if a > b else -1.0))
        label = base.get("label") or o.readout_fact
        moved = f"{label}: {b:,.4g} to {a:,.4g} ({change * 100:+.1f}%)"
        if abs(change) < FLAT:
            return "flat", moved
        better = change < 0 if o.direction == "down" else change > 0
        return ("improved" if better else "worse"), moved
    if last and base.get("check") in VERDICT_RANK and (last.get("checks") or {}).get(o.source_key) in VERDICT_RANK:
        b, a = VERDICT_RANK[base["check"]], VERDICT_RANK[last["checks"][o.source_key]]
        if a != b:
            return ("improved" if a > b else "worse"), f"{o.source_key}: {base['check']} to {last['checks'][o.source_key]}"
        return "flat", f"{o.source_key} is still {base['check']}"
    what = o.readout_fact or "a fact that maps to this card"
    return "unmeasured", f"no audit after approval measured {what}"


def read(session: Session, scope: Scope, run: m.AuditRun, now: Optional[datetime] = None) -> list[m.OpportunityOutcome]:
    """Add this run's readout to every outcome it can measure, and write verdicts that are due."""
    now = now or datetime.now(timezone.utc)
    rows = list(session.scalars(select(m.OpportunityOutcome).where(
        m.OpportunityOutcome.client_id == run.client_id, m.OpportunityOutcome.status == "tracking")))
    if not rows:
        return []
    facts = {f.fact_id: (f.value or {}).get("v") for f in session.scalars(select(m.AuditFact).where(m.AuditFact.run_id == run.id))}
    sc = session.get(m.Scorecard, run.scorecard_id) if run.scorecard_id else None
    verdicts = {cid: r.verdict for cid, r in results_of(session, sc).items()} if sc else {}
    touched = []
    for o in rows:
        changed = False
        if o.run_id != run.id and _aware(run.created_at) >= _aware(o.approved_at) and sc is not None \
                and not any(r.get("run_id") == run.id for r in o.readouts or []):
            v = facts.get(o.readout_fact) if o.readout_fact else None
            o.readouts = (o.readouts or []) + [{"run_id": run.id, "at": _aware(run.created_at).isoformat(),
                                                "v": v if _num(v) else None,
                                                "checks": {o.source_key: verdicts[o.source_key]} if o.source_key in verdicts else {}}]
            changed = True
        due = _aware(o.due_at)
        if now >= due and (o.readouts or now >= due + GRACE):
            o.status, o.verdict_reason = _verdict(o)
            o.decided_at = now
            events.record(session, scope, f"opportunity.{o.status}", o, f"{o.title}: {o.status} ({o.verdict_reason})")
            changed = True
        if changed:
            o.updated_at = now
            touched.append(o)
    session.flush()
    return touched


def track_record(session: Session, org_id: str) -> dict[str, dict[str, int]]:
    """{source_key: {improved, flat, worse, unmeasured}} over every decided outcome in the organization."""
    out: dict[str, dict[str, int]] = {}
    for key, status in session.execute(select(m.OpportunityOutcome.source_key, m.OpportunityOutcome.status).where(
            m.OpportunityOutcome.org_id == org_id, m.OpportunityOutcome.status.in_(DECIDED))):
        rec = out.setdefault(key, {k: 0 for k in DECIDED})
        rec[status] += 1
    return out
