"""Who is acting, and on which tenant. Every service call takes a Scope.

Services never read or write client data without a Scope whose client_id is set,
and they filter every query by it, so a missing filter can't leak another client's rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m

ActorKind = Literal["user", "agent", "system"]


class NotPermitted(Exception):
    """The actor may not do this. Carries a plain-language reason."""


class NotFound(Exception):
    """The object doesn't exist in this scope (or the actor can't see it: deliberately the same)."""


class InvalidState(Exception):
    """The object exists but isn't in a state that allows this change."""


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    id: str
    # For users: every role they hold for the client in scope (org-wide roles included).
    # For agents: exactly one role, the one it acts as.
    roles: frozenset[str] = field(default_factory=frozenset)

    @property
    def ref(self) -> str:
        return "system" if self.kind == "system" else f"{self.kind}:{self.id}"

    @property
    def is_human(self) -> bool:
        return self.kind == "user"

    @property
    def acting_role(self) -> str:
        return next(iter(sorted(self.roles)), "") if self.kind == "agent" else ""


SYSTEM = Actor("system", "system", frozenset())


@dataclass(frozen=True)
class Scope:
    org_id: str
    client_id: Optional[str]
    actor: Actor

    def require_client(self) -> str:
        if not self.client_id:
            raise NotPermitted("this needs a client workspace")
        return self.client_id


def user_roles(session: Session, user_id: str, client_id: Optional[str]) -> frozenset[str]:
    """Roles a user holds for a client: org-wide memberships plus that client's memberships."""
    q = select(m.Membership.role).where(m.Membership.user_id == user_id)
    if client_id:
        q = q.where((m.Membership.client_id.is_(None)) | (m.Membership.client_id == client_id))
    else:
        q = q.where(m.Membership.client_id.is_(None))
    return frozenset(session.scalars(q))


def scope_for_user(session: Session, user: m.User, client_id: Optional[str]) -> Scope:
    if not user.active:
        raise NotPermitted("this user is deactivated")
    if client_id:
        client = session.get(m.Client, client_id)
        if client is None or client.org_id != user.org_id:
            raise NotFound("client not found")
    roles = user_roles(session, user.id, client_id)
    if not roles:
        raise NotFound("client not found")  # no membership: indistinguishable from absent
    return Scope(user.org_id, client_id, Actor("user", user.id, roles))


def scope_for_agent(session: Session, agent: m.Agent, client_id: Optional[str]) -> Scope:
    if not agent.active:
        raise NotPermitted("this agent is deactivated")
    if client_id:
        client = session.get(m.Client, client_id)
        if client is None or client.org_id != agent.org_id:
            raise NotFound("client not found")
    return Scope(agent.org_id, client_id, Actor("agent", agent.id, frozenset({agent.role})))


def get_scoped(session: Session, scope: Scope, model, obj_id: str):
    """Fetch a client-owned row, or NotFound if it belongs to another tenant."""
    obj = session.get(model, obj_id)
    if obj is None or obj.org_id != scope.org_id or getattr(obj, "client_id", scope.client_id) != scope.client_id:
        raise NotFound(f"{model.__tablename__[:-1]} not found")
    return obj
