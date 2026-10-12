"""Email the app sends itself: the 30-day onboarding sequence and staff alerts.

lifecycle  which email is due for whom, from each brand's state; next_step() for the app
templates  the words; numbers only from the brand's stored audit and impact readings
render     blocks -> HTML and plain text
outbox     queue (rendered, unique per person and key) and deliver (claim, re-check, send)
providers  postmark, resend, or outbox (stored, never sent)
links      signed unsubscribe links
"""
from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from . import lifecycle, outbox


def tick(factory: sessionmaker[Session], session: Session, settings, vault) -> str:
    """The worker's pass: queue what's due (in the caller's transaction), then send what's queued."""
    from ..execution.providers import stored_key
    queued = lifecycle.plan(session, settings, vault)
    session.commit()
    sent = outbox.deliver(factory, settings, vault, key_for=lambda org, provider: stored_key(factory, vault, org, provider))
    parts = [f"{n} {k}" for k, n in sorted({**{f"queued {k}": v for k, v in queued.items()}, **sent}.items())]
    return ", ".join(parts)


__all__ = ["lifecycle", "outbox", "tick"]
