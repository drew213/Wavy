"""Self-serve brand accounts: sign up in the browser, sign in with a password.

A sign-up creates the brand's client workspace inside the agency's org and a client user who owns
it. That user can connect the brand's own Klaviyo account, run the onboarding audit and read their
audit; everything that needs the agency (approving the plan, building flows) waits for staff.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import events, tenancy
from . import models as m
from .scope import SYSTEM, InvalidState, Scope

MIN_PASSWORD = 10
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_N, _R, _P = 2 ** 14, 8, 1


class SignInFailed(Exception):
    """Wrong email or password. Deliberately doesn't say which."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def signup_org(session: Session, configured: Optional[str]) -> m.Organization:
    """The org sign-ups join: the configured slug, or the only org there is."""
    if configured:
        org = session.scalar(select(m.Organization).where(m.Organization.slug == configured))
        if org is None:
            raise InvalidState("sign-ups are pointed at an organization that doesn't exist")
        return org
    orgs = session.scalars(select(m.Organization).limit(2)).all()
    if len(orgs) != 1:
        raise InvalidState("sign-ups are off: set WAVYOS_SIGNUP_ORG to the organization brands should join")
    return orgs[0]


def signup_brand(session: Session, org: m.Organization, *, brand_name: str, person_name: str, email: str,
                 password: str, website: Optional[str] = None) -> tuple[m.Client, m.User, str]:
    """Create the brand's workspace and its owner. Returns (client, user, sign-in token)."""
    email = email.strip().lower()
    brand_name, person_name = brand_name.strip(), person_name.strip()
    if not brand_name or not person_name:
        raise ValueError("brand name and your name are both needed")
    if not _EMAIL.match(email):
        raise ValueError("that email address doesn't look right")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"use a password of at least {MIN_PASSWORD} characters")
    if session.scalar(select(m.User.id).where(m.User.org_id == org.id, func.lower(m.User.email) == email)):
        raise InvalidState("an account with this email already exists. Sign in instead")
    client = m.Client(org_id=org.id, name=brand_name, slug=_free_slug(session, org.id, brand_name),
                      timezone="America/New_York", website=website or None)
    session.add(client)
    session.flush()
    user = m.User(org_id=org.id, email=email, name=person_name, kind="client")
    session.add(user)
    session.flush()
    session.add(m.Membership(org_id=org.id, user_id=user.id, client_id=client.id, role="client_owner"))
    session.add(m.PasswordCredential(user_id=user.id, password_hash=hash_password(password)))
    scope = Scope(org.id, client.id, SYSTEM)
    events.record(session, scope, "client.created", client, f"{brand_name} (self-serve sign-up)")
    events.record(session, scope, "user.added", user, f"{email} as client_owner (self-serve sign-up)")
    return client, user, tenancy.issue_token(session, user, "browser sign-in")


def sign_in(session: Session, email: str, password: str) -> tuple[m.User, str]:
    email = email.strip().lower()
    for user in session.scalars(select(m.User).where(func.lower(m.User.email) == email, m.User.active.is_(True))):
        cred = session.scalar(select(m.PasswordCredential).where(m.PasswordCredential.user_id == user.id))
        if cred is not None and check_password(password, cred.password_hash):
            return user, tenancy.issue_token(session, user, "browser sign-in")
    hash_password(password)  # same cost whether or not the email exists
    raise SignInFailed("that email and password don't match an account")


def set_password(session: Session, user: m.User, password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"use a password of at least {MIN_PASSWORD} characters")
    cred = session.scalar(select(m.PasswordCredential).where(m.PasswordCredential.user_id == user.id))
    if cred is None:
        session.add(m.PasswordCredential(user_id=user.id, password_hash=hash_password(password)))
    else:
        cred.password_hash = hash_password(password)
    session.flush()


def _free_slug(session: Session, org_id: str, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "brand"
    if len(base) < 2:
        base = f"{base}-brand"
    slug, n = base, 2
    while session.scalar(select(m.Client.id).where(m.Client.org_id == org_id, m.Client.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug
