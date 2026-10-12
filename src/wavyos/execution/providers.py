"""Model provider credentials and the adapter set a worker uses for one organization.

An org's Anthropic key is sealed in the vault (bound to the org) and opened only inside the worker.
Without a stored key, the Anthropic SDK falls back to its own credential chain (ANTHROPIC_API_KEY or
an `ant auth login` profile). Codex uses the ChatGPT sign-in of the machine the worker runs on.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .. import events
from .. import models as m
from ..scope import NotPermitted, Scope
from ..vault import Vault
from .adapters import AnthropicAdapter, CodexAdapter

PROVIDERS = ("anthropic", "postmark", "resend")  # model providers, and the email providers wavyos.mail sends through


def _aad(org_id: str, provider: str) -> bytes:
    return f"wavyos/provider|{org_id}|{provider}".encode()


def store_key(session: Session, scope: Scope, vault: Vault, provider: str, secret: str) -> m.ProviderCredential:
    if not scope.actor.is_human or "org_admin" not in scope.actor.roles:
        raise NotPermitted("only an org admin can add a provider key")
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {PROVIDERS}")
    blob, key_ref = vault.seal(secret, _aad(scope.org_id, provider))
    row = session.scalar(select(m.ProviderCredential).where(m.ProviderCredential.org_id == scope.org_id,
                                                            m.ProviderCredential.provider == provider))
    if row is None:
        row = m.ProviderCredential(org_id=scope.org_id, provider=provider, secret_ciphertext=blob, key_ref=key_ref,
                                   created_by=scope.actor.ref)
        session.add(row)
    else:
        row.secret_ciphertext, row.key_ref = blob, key_ref
    events.record(session, Scope(scope.org_id, None, scope.actor), "provider.key_stored", row, provider)
    return row


def _key(factory: sessionmaker[Session], vault: Optional[Vault], org_id: str, provider: str) -> Optional[str]:
    if vault is None:
        return None
    with factory() as s:
        row = s.scalar(select(m.ProviderCredential).where(m.ProviderCredential.org_id == org_id,
                                                          m.ProviderCredential.provider == provider))
        if row is None:
            return None
        with vault.open(row.secret_ciphertext, row.key_ref, _aad(org_id, provider)) as k:
            return k


def stored_key(factory: sessionmaker[Session], vault: Optional[Vault], org_id: str, provider: str) -> Optional[str]:
    """An org's stored key for one provider, or None. Only the worker calls this, at the moment it needs the key."""
    return _key(factory, vault, org_id, provider)


def adapter_factory(factory: sessionmaker[Session], vault: Optional[Vault], extra: Optional[dict] = None):
    """Returns org_id -> {executor name: adapter}; built per org so each uses its own key."""
    cache: dict[str, dict] = {}

    def for_org(org_id: str) -> dict:
        if org_id not in cache:
            cache[org_id] = {"anthropic": AnthropicAdapter(api_key=_key(factory, vault, org_id, "anthropic")),
                             "codex": CodexAdapter(), **(extra or {})}
        return cache[org_id]
    return for_org
