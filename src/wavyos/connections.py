"""Connected accounts (Klaviyo first). Secrets go in through the vault and never come back out
through the API; code that needs one uses `with secret(...)`, which authorizes and logs the use.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import events
from . import models as m
from .db import utcnow
from .policy import authorize
from .scope import InvalidState, NotPermitted, Scope, get_scoped
from .vault import Vault, binding

PROVIDERS = ("klaviyo", "shopify")


def add(session: Session, scope: Scope, vault: Vault, *, provider: str, external_account_id: str,
        secret: str, label: str = "", scopes: list[str] | None = None) -> m.Connection:
    client_id = scope.require_client()
    auth = authorize(session, scope, "connection.manage")
    if not auth.allowed:
        raise NotPermitted(auth.reason)
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    if not external_account_id:
        raise ValueError("confirm the account ID first (connectors verify it before saving)")
    clash = session.scalar(select(m.Connection).where(
        m.Connection.client_id == client_id, m.Connection.provider == provider,
        m.Connection.external_account_id == external_account_id, m.Connection.status == "active"))
    if clash is not None:
        raise InvalidState("this account is already connected; revoke the old connection first")
    blob, key_ref = vault.seal(secret, binding(scope.org_id, client_id, provider, external_account_id))
    c = m.Connection(org_id=scope.org_id, client_id=client_id, provider=provider, external_account_id=external_account_id,
                     label=label, secret_ciphertext=blob, key_ref=key_ref, scopes=scopes or ["read"],
                     created_by=scope.actor.ref, last_checked_at=utcnow())
    session.add(c)
    events.record(session, scope, "connection.added", c, f"{provider} {external_account_id}", {"scopes": c.scopes})
    return c


@contextmanager
def secret(session: Session, scope: Scope, vault: Vault, connection_id: str, purpose: str) -> Iterator[str]:
    """Plaintext for one use. Logged with its purpose; the secret itself never is."""
    auth = authorize(session, scope, "data.read")
    if not auth.allowed:
        raise NotPermitted(auth.reason)
    c = get_scoped(session, scope, m.Connection, connection_id)
    if c.status != "active":
        raise InvalidState(f"connection is {c.status}")
    events.record(session, scope, "connection.used", c, purpose)
    with vault.open(c.secret_ciphertext, c.key_ref, binding(c.org_id, c.client_id, c.provider, c.external_account_id)) as s:
        yield s


def revoke(session: Session, scope: Scope, connection_id: str, reason: str) -> m.Connection:
    auth = authorize(session, scope, "connection.manage")
    if not auth.allowed:
        raise NotPermitted(auth.reason)
    c = get_scoped(session, scope, m.Connection, connection_id)
    if c.status == "revoked":
        return c
    c.status, c.revoked_at = "revoked", utcnow()
    c.secret_ciphertext = b""  # destroy our copy; the provider-side key must also be deleted
    events.record(session, scope, "connection.revoked", c, reason)
    return c


def active(session: Session, scope: Scope, provider: str) -> list[m.Connection]:
    return list(session.scalars(select(m.Connection).where(
        m.Connection.org_id == scope.org_id, m.Connection.client_id == scope.require_client(),
        m.Connection.provider == provider, m.Connection.status == "active")))
