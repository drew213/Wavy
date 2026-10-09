"""Approval requests and decisions.

An approval is bound to one action, one subject and the hash of the exact payload shown to
the approver. Only a person holding one of the action's approver roles can decide it, and
never the person (or agent) who asked for it.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import events
from . import models as m
from .db import utcnow
from .policy import ACTIONS, _aware, authorize, payload_hash
from .scope import InvalidState, NotPermitted, Scope, get_scoped

# subject_kind -> fn(session, scope, approval, approved: bool). Registered by the owning module.
ON_DECIDED: dict[str, Callable[[Session, Scope, m.Approval, bool], None]] = {}


def on_decided(subject_kind: str):
    def deco(fn):
        ON_DECIDED[subject_kind] = fn
        return fn
    return deco


def request(session: Session, scope: Scope, action: str, subject_kind: str, subject_id: str,
            summary: str, payload: Any, expires_in: Optional[timedelta] = timedelta(days=14)) -> m.Approval:
    client_id = scope.require_client()
    if action not in ACTIONS or ACTIONS[action].level != "explicit_approval":
        raise ValueError(f"{action} isn't an approval action")
    auth = authorize(session, scope, "approval.request")
    if not auth.allowed:
        raise NotPermitted(auth.reason)
    ap = m.Approval(
        org_id=scope.org_id, client_id=client_id, action=action, subject_kind=subject_kind,
        subject_id=subject_id, summary=summary, payload=payload if isinstance(payload, dict) else {"value": payload},
        payload_hash=payload_hash(payload), requested_by=scope.actor.ref,
        expires_at=(utcnow() + expires_in) if expires_in else None,
    )
    session.add(ap)
    events.record(session, scope, "approval.requested", ap, summary, {"action": action, "subject": f"{subject_kind}:{subject_id}"})
    return ap


def decide(session: Session, scope: Scope, approval_id: str, approve: bool, note: str = "") -> m.Approval:
    from . import knowledge, operations  # noqa: F401  (registers the ON_DECIDED handlers)
    ap = get_scoped(session, scope, m.Approval, approval_id)
    actor = scope.actor
    if not actor.is_human:
        raise NotPermitted("only a person can approve or reject")
    allowed = ACTIONS[ap.action].approvers
    if not (actor.roles & allowed):
        raise NotPermitted(f"approving {ap.action} needs one of: {', '.join(sorted(allowed))}")
    if ap.requested_by == actor.ref:
        raise NotPermitted("you can't approve your own request")
    if ap.status != "pending":
        raise InvalidState(f"this approval is already {ap.status}")
    if ap.expires_at is not None and _aware(ap.expires_at) <= utcnow():
        ap.status = "expired"
        events.record(session, scope, "approval.expired", ap, "expired before a decision")
        raise InvalidState("this approval expired; request it again")
    ap.status = "approved" if approve else "rejected"
    ap.decided_by, ap.decided_at, ap.note = actor.ref, utcnow(), note
    events.record(session, scope, f"approval.{ap.status}", ap, note or ap.summary, {"action": ap.action})
    handler = ON_DECIDED.get(ap.subject_kind)
    if handler:
        handler(session, scope, ap, approve)
    return ap


def pending(session: Session, scope: Scope) -> list[m.Approval]:
    return list(session.scalars(select(m.Approval).where(
        m.Approval.org_id == scope.org_id, m.Approval.client_id == scope.require_client(),
        m.Approval.status == "pending").order_by(m.Approval.created_at)))
