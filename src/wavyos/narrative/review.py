"""The strategist queue: each draft beside its evidence, with approve, edit or reject.

Approve and reject are decisions on the draft's approval (policy action narrative.approve), so the usual
rules hold: only a person with an approver role decides, never the one who asked. An edit is facts-checked
like a draft (a number not in the ledger is refused, never fixed), keeps the original and the diff, and
publishes as "edited". A needs_rewrite draft can only publish through an edit.

Every decision republishes the run's workspace bundle, so the brand sees approved sections immediately.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import approvals, events
from .. import models as m
from ..analysis.messages import _terms
from ..db import utcnow
from ..policy import ACTIONS
from ..scope import InvalidState, NotPermitted, Scope, get_scoped
from . import facts_check, packet
from .drafting import APPROVAL_ACTION

PUBLISHED = ("approved", "edited")


class FactsCheckFailed(ValueError):
    def __init__(self, problems: list[dict]):
        super().__init__(f"{len(problems)} facts-check problem(s); nothing was saved")
        self.problems = problems


def diff(before: Any, after: Any, path: str = "") -> list[dict]:
    """Changed leaves between two JSON values, as {path, before, after}."""
    if isinstance(before, dict) and isinstance(after, dict):
        out = []
        for k in list(before) + [k for k in after if k not in before]:
            out += diff(before.get(k), after.get(k), f"{path}.{k}" if path else str(k))
        return out
    if isinstance(before, list) and isinstance(after, list):
        out = []
        for i in range(max(len(before), len(after))):
            out += diff(before[i] if i < len(before) else None, after[i] if i < len(after) else None, f"{path}[{i}]")
        return out
    return [] if before == after else [{"path": path, "before": before, "after": after}]


def republish(session: Session, run_id: str) -> None:
    from .. import workspace
    run = session.get(m.AuditRun, run_id)
    if run is not None and run.scorecard_id:
        workspace.publish(session, run)


def _require_reviewer(scope: Scope) -> None:
    if not scope.actor.is_human or not (scope.actor.roles & ACTIONS[APPROVAL_ACTION].approvers):
        raise NotPermitted("reviewing a written section needs one of: "
                           + ", ".join(sorted(ACTIONS[APPROVAL_ACTION].approvers)))


def view(session: Session, row: m.NarrativeDraft) -> dict:
    """The draft beside its evidence: every fact it cites (value and basis) and the section's findings."""
    ledger = {f.fact_id: f for f in session.scalars(select(m.AuditFact).where(m.AuditFact.run_id == row.run_id))}
    cited = list(dict.fromkeys(fid for ins in (row.payload or {}).get("insights") or [] for fid in ins.get("facts") or []))
    ap = session.get(m.Approval, row.approval_id) if row.approval_id else None
    return {
        "id": row.id, "run_id": row.run_id, "section": row.section, "status": row.status, "reason": row.reason,
        "payload": row.payload, "original": row.original, "diff": row.diff, "facts_check": row.facts_check,
        "critic": row.critic, "score": row.score, "rounds": row.rounds, "drafter": row.drafter,
        "critic_by": row.critic_by, "cost_usd": row.cost_usd, "packet_hash": row.packet_hash,
        "approval": {"id": ap.id, "status": ap.status} if ap else None,
        "reviewer": row.reviewer, "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_note": row.review_note,
        "evidence": [{"id": fid, **({"label": f.label, "value": (f.value or {}).get("v"), "unit": f.unit, "window": f.window,
                                     "source": f.source, "evidence_class": f.evidence_class} if (f := ledger.get(fid))
                                    else {"missing": True})} for fid in cited],
    }


def decide(session: Session, scope: Scope, draft_id: str, approve: bool, note: str = "") -> m.NarrativeDraft:
    _require_reviewer(scope)
    row = get_scoped(session, scope, m.NarrativeDraft, draft_id)
    ap = session.get(m.Approval, row.approval_id) if row.approval_id else None
    if ap is None or ap.status != "pending":
        if not approve and row.status in ("needs_rewrite", "skipped", "draft"):
            _close(session, scope, row, "rejected", note)
            return row
        raise InvalidState("this draft isn't waiting for approval" +
                           ("; edit it until it passes the facts check" if row.status == "needs_rewrite" else ""))
    approvals.decide(session, scope, ap.id, approve, note)
    return row


def edit(session: Session, scope: Scope, draft_id: str, payload: dict, note: str = "") -> m.NarrativeDraft:
    _require_reviewer(scope)
    row = get_scoped(session, scope, m.NarrativeDraft, draft_id)
    if row.status == "drafting":
        raise InvalidState("this section is still being drafted")
    run = session.get(m.AuditRun, row.run_id)
    ctx = packet.context(session, run)
    check = facts_check.check(row.section, payload, ctx["ledger"], terms=_terms(ctx["answers"]),
                              card_ids={c["id"] for c in ctx["summary"].get("opportunities") or []}
                              if row.section == "opportunities" else None)
    if not check["ok"]:
        raise FactsCheckFailed(check["problems"])
    edited = check.pop("draft")
    if row.original is None:
        row.original = row.payload
    row.diff = diff(row.original, edited)
    row.payload = edited
    row.facts_check = {**check, "by": "strategist edit"}
    ap = session.get(m.Approval, row.approval_id) if row.approval_id else None
    if ap is not None and ap.status == "pending":
        approvals.decide(session, scope, ap.id, True, note or "approved with edits")
    else:
        _close(session, scope, row, "edited", note)
    return row


def _close(session: Session, scope: Scope, row: m.NarrativeDraft, status: str, note: str) -> None:
    row.status, row.reviewer, row.reviewed_at, row.review_note = status, scope.actor.ref, utcnow(), note
    row.updated_at = utcnow()
    events.record(session, scope, f"narrative.{status}", row, f"{row.section}: {status}" + (f": {note}" if note else ""),
                  {"section": row.section, "changes": len(row.diff or [])})
    session.flush()
    republish(session, row.run_id)


@approvals.on_decided("narrative_draft")
def _decided(session: Session, scope: Scope, ap: m.Approval, approved: bool) -> None:
    row = get_scoped(session, scope, m.NarrativeDraft, ap.subject_id)
    if row.approval_id != ap.id:
        return
    _close(session, scope, row, ("edited" if row.original is not None else "approved") if approved else "rejected",
           ap.note or "")


def narrative_for(session: Session, run_id: str) -> dict[str, m.NarrativeDraft]:
    return {r.section: r for r in session.scalars(select(m.NarrativeDraft).where(m.NarrativeDraft.run_id == run_id))}


def published(row: Optional[m.NarrativeDraft]) -> Optional[dict]:
    if row is None or row.status not in PUBLISHED:
        return None
    return {**(row.payload or {}), "status": row.status,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None}
