"""Layers 1 and 2: agency standards and client facts.

Standards are versioned and never edited in place. Client facts are proposed with a source and
become part of the client's knowledge only when an approver says so. Agents read approved facts
only, so a meeting note can't quietly turn into a brand rule.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import approvals, events
from . import models as m
from .db import utcnow
from .policy import authorize
from .scope import InvalidState, NotPermitted, Scope, get_scoped
from .taxonomy import require_area, require_component


def _need(session, scope, action, **kw):
    auth = authorize(session, scope, action, **kw)
    if not auth.allowed:
        raise NotPermitted(auth.reason)
    return auth


# ---------------------------------------------------------------- standards (layer 1)

def publish_standard(session: Session, scope: Scope, key: str, area: str, component: str, title: str,
                     body: str = "", data: Optional[dict] = None, source: str = "") -> m.Standard:
    """Add a new version of an agency standard; the previous active version is superseded."""
    if scope.client_id:
        raise NotPermitted("standards belong to the agency, not a client workspace")
    _need(session, scope, "standard.publish")
    require_area(area), require_component(component)
    current = session.scalar(select(m.Standard).where(
        m.Standard.org_id == scope.org_id, m.Standard.key == key, m.Standard.status == "active"))
    version = (session.scalar(select(func.max(m.Standard.version)).where(
        m.Standard.org_id == scope.org_id, m.Standard.key == key)) or 0) + 1
    if current is not None:
        if current.body == body and current.data == (data or {}) and current.title == title:
            return current  # unchanged: don't mint a version
        current.status = "superseded"
    s = m.Standard(org_id=scope.org_id, key=key, version=version, area=area, component=component, title=title,
                   body=body, data=data or {}, source=source, created_by=scope.actor.ref)
    session.add(s)
    events.record(session, scope, "standard.published", s, f"{key} v{version}", {"key": key, "version": version})
    return s


def standards(session: Session, org_id: str, area: Optional[str] = None) -> list[m.Standard]:
    q = select(m.Standard).where(m.Standard.org_id == org_id, m.Standard.status == "active")
    if area:
        q = q.where(m.Standard.area == area)
    return list(session.scalars(q.order_by(m.Standard.area, m.Standard.key)))


# ---------------------------------------------------------------- client facts (layer 2)

def propose_fact(session: Session, scope: Scope, *, area: str, component: str, key: str, title: str,
                 value: Optional[dict] = None, body: str = "", source_kind: str, source_ref: str = "",
                 review_by: Optional[date] = None) -> tuple[m.ClientFact, m.Approval]:
    """Record a proposed fact and ask for its approval. Returns (fact, approval)."""
    client_id = scope.require_client()
    _need(session, scope, "fact.propose")
    require_area(area), require_component(component)
    if source_kind not in m.FACT_SOURCES:
        raise ValueError(f"source_kind must be one of {m.FACT_SOURCES}")
    if not source_ref and source_kind != "person":
        raise ValueError("say where this fact came from (source_ref)")
    supersedes = session.scalar(select(m.ClientFact.id).where(
        m.ClientFact.client_id == client_id, m.ClientFact.key == key, m.ClientFact.status == "approved"))
    fact = m.ClientFact(org_id=scope.org_id, client_id=client_id, area=area, component=component, key=key,
                        title=title, value=value or {}, body=body, source_kind=source_kind, source_ref=source_ref,
                        proposed_by=scope.actor.ref, review_by=review_by, supersedes_id=supersedes)
    session.add(fact)
    events.record(session, scope, "fact.proposed", fact, title, {"key": key, "source": f"{source_kind}:{source_ref}"})
    ap = approvals.request(session, scope, "fact.approve", "client_fact", fact.id, f"Add to client knowledge: {title}",
                           {"key": key, "title": title, "value": fact.value, "body": body})
    fact.approval_id = ap.id
    return fact, ap


@approvals.on_decided("client_fact")
def _fact_decided(session: Session, scope: Scope, ap: m.Approval, approved: bool) -> None:
    fact = get_scoped(session, scope, m.ClientFact, ap.subject_id)
    if fact.status != "proposed":
        raise InvalidState(f"fact is already {fact.status}")
    if not approved:
        fact.status = "rejected"
        events.record(session, scope, "fact.rejected", fact, fact.title)
        return
    fact.status, fact.verified_by, fact.verified_at = "approved", ap.decided_by, utcnow()
    if fact.supersedes_id:
        old = session.get(m.ClientFact, fact.supersedes_id)
        if old is not None and old.status == "approved":
            old.status = "retired"
            events.record(session, scope, "fact.retired", old, f"superseded by {fact.id}")
    events.record(session, scope, "fact.approved", fact, fact.title, {"key": fact.key})


def facts(session: Session, scope: Scope, area: Optional[str] = None, include_proposed: bool = False) -> list[m.ClientFact]:
    """The client's knowledge. Approved facts only unless include_proposed (never for agents)."""
    if include_proposed and not scope.actor.is_human:
        raise NotPermitted("agents work from approved knowledge only")
    _need(session, scope, "data.read", log=False)
    q = select(m.ClientFact).where(m.ClientFact.org_id == scope.org_id, m.ClientFact.client_id == scope.require_client())
    q = q.where(m.ClientFact.status.in_(("approved", "proposed") if include_proposed else ("approved",)))
    if area:
        q = q.where(m.ClientFact.area == area)
    return list(session.scalars(q.order_by(m.ClientFact.area, m.ClientFact.key)))


def stale_facts(session: Session, scope: Scope, today: Optional[date] = None) -> list[m.ClientFact]:
    """Approved facts past their review date, or never given one."""
    today = today or utcnow().date()
    return [f for f in facts(session, scope) if f.review_by is None or f.review_by < today]


def context_pack(session: Session, scope: Scope, area: str) -> dict[str, Any]:
    """What an agent needs to work in one area: the agency standard plus this client's approved facts."""
    require_area(area)
    return {
        "area": area,
        "standards": [{"key": s.key, "version": s.version, "component": s.component, "title": s.title, "body": s.body}
                      for s in standards(session, scope.org_id, area)],
        "client_facts": [{"key": f.key, "component": f.component, "title": f.title, "value": f.value, "body": f.body,
                          "verified_at": f.verified_at.isoformat() if f.verified_at else None,
                          "review_by": f.review_by.isoformat() if f.review_by else None}
                         for f in facts(session, scope, area)],
    }
