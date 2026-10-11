"""Request plumbing: one transaction per request, bearer-token auth, tenant scope, services.

Routes stay thin: they resolve who is asking and on which client, then call a service.
Every permission rule lives in the services and the policy engine, never here.
"""
from __future__ import annotations

from typing import Annotated, Any, Callable, Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from .. import policy, tenancy
from ..scope import NotFound, NotPermitted, Scope, scope_for_user


class ServiceUnavailable(Exception):
    """An optional module (audit engine, connector) isn't installed in this build."""


# ---------------------------------------------------------------- transaction

def get_session(request: Request) -> Iterator[Session]:
    """Commit when the route returns, roll back when it raises. Runs before the response is sent
    (scope="function"), so a failed commit is a 500, never a 200 for unsaved work."""
    session = request.app.state.factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session, scope="function")]


# ---------------------------------------------------------------- auth

def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    return token.strip()


def current_user(request: Request, session: SessionDep) -> m.User:
    user = tenancy.user_for_token(session, _bearer(request))
    if user is None:
        raise HTTPException(401, "invalid or revoked token", headers={"WWW-Authenticate": "Bearer"})
    return user


UserDep = Annotated[m.User, Depends(current_user)]


# ---------------------------------------------------------------- scope

def resolve_client(session: Session, user: m.User, client_slug: str) -> m.Client:
    """Find a client by slug inside the user's organization only."""
    c = session.scalar(select(m.Client).where(m.Client.org_id == user.org_id, m.Client.slug == client_slug))
    if c is None:
        raise NotFound("client not found")
    return c


def client_scope(client_slug: str, session: SessionDep, user: UserDep) -> Scope:
    client = resolve_client(session, user, client_slug)
    return scope_for_user(session, user, client.id)  # NotFound without a membership


def org_scope(session: SessionDep, user: UserDep) -> Scope:
    try:
        return scope_for_user(session, user, None)
    except NotFound:
        raise NotPermitted("this needs an agency-wide role") from None


ClientScope = Annotated[Scope, Depends(client_scope)]
OrgScope = Annotated[Scope, Depends(org_scope)]


def require(session: Session, scope: Scope, action: str) -> None:
    """Ask the policy engine (without logging) for read endpoints whose service has no check of its own."""
    auth = policy.authorize(session, scope, action, log=False)
    if not auth.allowed:
        raise NotPermitted(auth.reason)


# ---------------------------------------------------------------- injectable services

def _default_klaviyo_verify() -> Callable[[str], dict]:
    from ..connectors.klaviyo import verify_account
    return verify_account


def _default_run_audit() -> Callable[..., Any]:
    from ..audit.engine import run_audit
    return run_audit


def _default_compare() -> Callable[..., Any]:
    from ..audit.engine import compare
    return compare


def _default_vault(services: dict):
    from ..vault import Vault, key_provider
    return Vault(key_provider(services["settings"]))


DEFAULTS: dict[str, Callable[[dict], Any]] = {
    "klaviyo_verify": lambda s: _default_klaviyo_verify(),
    "run_audit": lambda s: _default_run_audit(),
    "compare_audits": lambda s: _default_compare(),
    "vault": _default_vault,
    "audit_output_root": lambda s: s["settings"].audit_output_root,
}


def service(request: Request, name: str) -> Any:
    """app.state.services[name], or the lazily built default (cached). Tests inject fakes."""
    services: dict = request.app.state.services
    if name in services:
        return services[name]
    try:
        value = DEFAULTS[name](services)
    except ModuleNotFoundError as e:
        raise ServiceUnavailable(f"{name} isn't available in this build ({e.name} is not installed)") from None
    services[name] = value
    return value
