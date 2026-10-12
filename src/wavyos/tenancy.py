"""Organizations, client workspaces, people, agents and API tokens."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import events
from . import models as m
from .db import utcnow
from .scope import SYSTEM, InvalidState, NotPermitted, Scope

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
TOKEN_PREFIX = "wos_"


def _slug(s: str) -> str:
    if not SLUG.match(s):
        raise ValueError("slug: lowercase letters, digits and dashes, 2-63 characters")
    return s


def bootstrap_org(session: Session, *, name: str, slug: str, admin_email: str, admin_name: str) -> tuple[m.Organization, m.User]:
    """Create an organization, its first admin, and one agent per role."""
    if session.scalar(select(m.Organization).where(m.Organization.slug == slug)):
        raise InvalidState(f"organization {slug} already exists")
    org = m.Organization(name=name, slug=_slug(slug))
    session.add(org)
    session.flush()
    admin = m.User(org_id=org.id, email=admin_email.lower(), name=admin_name, kind="staff")
    session.add(admin)
    session.flush()
    session.add(m.Membership(org_id=org.id, user_id=admin.id, client_id=None, role="org_admin"))
    for role in m.AGENT_ROLES:
        session.add(m.Agent(org_id=org.id, role=role, name=f"{slug}-{role}"))
    scope = Scope(org.id, None, SYSTEM)
    events.record(session, scope, "org.created", org, name)
    events.record(session, scope, "user.added", admin, f"{admin_email} as org_admin")
    return org, admin


def _require_lead(scope: Scope) -> None:
    if not scope.actor.is_human or not (scope.actor.roles & {"org_admin", "account_lead"}):
        raise NotPermitted("only an org admin or account lead can do this")


def create_client(session: Session, scope: Scope, *, name: str, slug: str, timezone: str = "UTC",
                  website: Optional[str] = None) -> m.Client:
    _require_lead(scope)
    ZoneInfo(timezone)  # raises for an unknown zone
    if session.scalar(select(m.Client).where(m.Client.org_id == scope.org_id, m.Client.slug == slug)):
        raise InvalidState(f"client {slug} already exists")
    c = m.Client(org_id=scope.org_id, name=name, slug=_slug(slug), timezone=timezone, website=website)
    session.add(c)
    session.flush()
    events.record(session, Scope(scope.org_id, c.id, scope.actor), "client.created", c, name)
    return c


def add_user(session: Session, scope: Scope, *, email: str, name: str, kind: str = "staff") -> m.User:
    if "org_admin" not in scope.actor.roles or not scope.actor.is_human:
        raise NotPermitted("only an org admin can add people")
    if kind not in m.USER_KINDS:
        raise ValueError(f"kind must be one of {m.USER_KINDS}")
    u = m.User(org_id=scope.org_id, email=email.lower(), name=name, kind=kind)
    session.add(u)
    session.flush()
    events.record(session, Scope(scope.org_id, None, scope.actor), "user.added", u, f"{email} ({kind})")
    return u


def grant(session: Session, scope: Scope, *, user_id: str, role: str, client_id: Optional[str]) -> m.Membership:
    """Org admins grant anything; account leads grant client roles on their clients."""
    user = session.get(m.User, user_id)
    if user is None or user.org_id != scope.org_id:
        raise NotPermitted("user not found")
    if role in m.STAFF_ROLES:
        if user.kind != "staff":
            raise NotPermitted("client stakeholders can't hold staff roles")
        if "org_admin" not in scope.actor.roles:
            raise NotPermitted("only an org admin can grant staff roles")
    elif role in m.CLIENT_ROLES:
        if not client_id:
            raise ValueError("client roles belong to one client")
        _require_lead(scope)
    else:
        raise ValueError(f"unknown role {role}")
    if client_id:
        c = session.get(m.Client, client_id)
        if c is None or c.org_id != scope.org_id:
            raise NotPermitted("client not found")
    mb = m.Membership(org_id=scope.org_id, user_id=user_id, client_id=client_id, role=role)
    session.add(mb)
    session.flush()
    events.record(session, Scope(scope.org_id, client_id, scope.actor), "role.granted", mb, f"{user.email}: {role}")
    return mb


# ---------------------------------------------------------------- API tokens

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(session: Session, user: m.User, label: str = "", ttl: Optional[timedelta] = None) -> str:
    """Returns the plaintext token once. Only its hash is stored. Without a ttl the token lasts until revoked."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    session.add(m.ApiToken(user_id=user.id, prefix=token[:12], token_hash=_digest(token), label=label,
                           expires_at=utcnow() + ttl if ttl else None))
    session.flush()
    events.record(session, Scope(user.org_id, None, SYSTEM), "token.issued", user, label or "api token")
    return token


def _token_row(session: Session, token: str) -> Optional[m.ApiToken]:
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    digest = _digest(token)
    for row in session.scalars(select(m.ApiToken).where(m.ApiToken.prefix == token[:12], m.ApiToken.revoked_at.is_(None))):
        if hmac.compare_digest(row.token_hash, digest):
            return row
    return None


def user_for_token(session: Session, token: str) -> Optional[m.User]:
    row = _token_row(session, token)
    if row is None:
        return None
    if row.expires_at is not None:
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if expires <= utcnow():
            return None
    row.last_used_at = utcnow()
    user = session.get(m.User, row.user_id)
    return user if user and user.active else None


def end_session(session: Session, token: str, label: str) -> bool:
    """Signs this token out if it carries this label. Returns whether a live token was revoked."""
    row = _token_row(session, token)
    if row is None or row.label != label:
        return False
    row.revoked_at = utcnow()
    return True


def end_sessions(session: Session, user: m.User, label: str) -> int:
    """Signs a person out of every browser at once: revokes all their tokens with this label."""
    rows = session.scalars(select(m.ApiToken).where(m.ApiToken.user_id == user.id, m.ApiToken.label == label,
                                                    m.ApiToken.revoked_at.is_(None))).all()
    for r in rows:
        r.revoked_at = utcnow()
    if rows:
        events.record(session, Scope(user.org_id, None, SYSTEM), "token.revoked", user,
                      f"signed out of {len(rows)} browser session(s)")
    return len(rows)


def revoke_token(session: Session, user: m.User, prefix: str) -> int:
    rows = session.scalars(select(m.ApiToken).where(m.ApiToken.user_id == user.id, m.ApiToken.prefix == prefix,
                                                    m.ApiToken.revoked_at.is_(None))).all()
    for r in rows:
        r.revoked_at = utcnow()
    return len(rows)
