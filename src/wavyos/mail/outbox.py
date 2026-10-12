"""Queueing and delivering emails (outbound_emails).

queue() renders an email for one person and stores it; the (person, key) pair is unique, so a race or a
second pass can't send the same email twice. deliver() claims queued rows one at a time (queued -> sending,
so two workers never both send one), re-checks opt-out, a paused brand and staleness, then sends.

Nudges wait out quiet hours (9pm-8am in the brand's timezone) and stop for anyone who opted out.
Transactional emails (audit started, audit ready, a key problem) and staff alerts always send.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .. import models as m
from ..db import utcnow
from . import links, render
from .providers import Message, SendFailed, make

QUIET_FROM, QUIET_UNTIL = 21, 8
NUDGE_GAP = timedelta(hours=20)      # at most one nudge per person in this long
STALE = timedelta(days=2)            # an email that couldn't go out within this long is dropped, not sent late
MAX_ATTEMPTS = 4
FOOTER = "Wavy Studios · You're getting this because your brand has a Wavy OS account."
STAFF_FOOTER = "Wavy OS · internal alert"


def aware(dt: Optional[datetime]) -> Optional[datetime]:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def paused(client: Optional[m.Client]) -> bool:
    return bool(client is not None and (client.settings or {}).get("emails_paused"))


def send_time(now: datetime, tz: str, kind: str) -> datetime:
    """Now, or for a nudge inside quiet hours, 8am the next morning in the brand's timezone."""
    if kind != "nudge":
        return now
    try:
        zone = ZoneInfo(tz or "UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    local = now.astimezone(zone)
    if QUIET_UNTIL <= local.hour < QUIET_FROM:
        return now
    morning = local.replace(hour=QUIET_UNTIL, minute=0, second=0, microsecond=0)
    if local.hour >= QUIET_FROM:
        morning += timedelta(days=1)
    return morning.astimezone(timezone.utc)


def last_nudge(session: Session, user_id: str) -> Optional[datetime]:
    return aware(session.scalar(select(m.OutboundEmail.send_after).where(
        m.OutboundEmail.user_id == user_id, m.OutboundEmail.kind == "nudge",
        m.OutboundEmail.status.in_(("queued", "sending", "sent"))).order_by(m.OutboundEmail.send_after.desc()).limit(1)))


def exists(session: Session, user_id: str, key: str) -> bool:
    return session.scalar(select(m.OutboundEmail.id).where(m.OutboundEmail.user_id == user_id,
                                                            m.OutboundEmail.key == key)) is not None


def queue(session: Session, *, user: m.User, client: Optional[m.Client], key: str, kind: str, email: render.Email,
          settings, vault, now: Optional[datetime] = None, data: Optional[dict] = None) -> Optional[m.OutboundEmail]:
    """Render and store one email for one person. Returns None when this person already has it."""
    now = now or utcnow()
    if exists(session, user.id, key):
        return None
    unsub = links.unsubscribe_url(settings.app_url, vault, user.id) if kind != "staff" else None
    headers = {"List-Unsubscribe": f"<{unsub}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"} if unsub else {}
    footer = STAFF_FOOTER if kind == "staff" else FOOTER
    row = m.OutboundEmail(
        org_id=user.org_id, client_id=client.id if client else None, user_id=user.id, key=key, kind=kind,
        to_email=user.email, subject=email.subject,
        html=render.html(email, app_url=settings.app_url, footer=footer, unsubscribe_url=unsub),
        text=render.text(email, footer=footer, unsubscribe_url=unsub),
        status="suppressed" if kind == "nudge" and user.email_opt_out_at else "queued",
        send_after=send_time(now, client.timezone if client else "UTC", kind),
        data={**(data or {}), "headers": headers, "preheader": email.preheader})
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        return None
    return row


def _claim(factory: sessionmaker[Session], row_id: str) -> bool:
    with factory() as s:
        n = s.execute(update(m.OutboundEmail).where(m.OutboundEmail.id == row_id, m.OutboundEmail.status == "queued")
                      .values(status="sending")).rowcount
        s.commit()
        return n == 1


def deliver(factory: sessionmaker[Session], settings, vault, *, now: Optional[datetime] = None, limit: int = 50,
            key_for: Optional[Callable[[str, str], Optional[str]]] = None, transport=None) -> dict:
    """Send what's due. Returns counts by result for the worker log.

    key_for(org_id, provider) returns the provider's API key from the vault (None when nobody stored one).
    """
    now = now or utcnow()
    counts: dict[str, int] = {}
    senders: dict[str, object] = {}
    with factory() as s:
        due = s.scalars(select(m.OutboundEmail.id).where(m.OutboundEmail.status == "queued", m.OutboundEmail.send_after <= now)
                        .order_by(m.OutboundEmail.send_after).limit(limit)).all()
    for row_id in due:
        if not _claim(factory, row_id):
            continue
        with factory() as s:
            row = s.get(m.OutboundEmail, row_id)
            user, client = s.get(m.User, row.user_id), (s.get(m.Client, row.client_id) if row.client_id else None)
            skip = ("this person opted out of onboarding emails" if row.kind == "nudge" and user.email_opt_out_at else
                    "emails are paused for this brand" if row.kind != "staff" and paused(client) else
                    "this person's account is inactive" if not user.active else
                    "not sent in time; dropped rather than sent late" if now - aware(row.send_after) > STALE else "")
            if skip:
                row.status, row.error = ("suppressed" if "opted out" in skip else "skipped"), skip
                s.commit()
                counts[row.status] = counts.get(row.status, 0) + 1
                continue
            if row.org_id not in senders:
                senders[row.org_id] = make(settings.email_provider,
                                           key_for(row.org_id, settings.email_provider) if key_for else None, transport)
            sender = senders[row.org_id]
            if sender is None:
                row.status = "queued"
                s.commit()
                counts["waiting_for_provider_key"] = counts.get("waiting_for_provider_key", 0) + 1
                continue
            msg = Message(to=row.to_email, subject=row.subject, html=row.html, text=row.text, sender=settings.email_from,
                          reply_to=settings.email_reply_to, headers=(row.data or {}).get("headers") or {}, tag=row.key)
            row.attempts += 1
            try:
                row.provider_id = sender.send(msg, row.id)
                row.status, row.sent_at, row.provider, row.error = "sent", now, sender.name, ""
            except Exception as e:  # anything else is retried too, so a row is never left in "sending"
                retry = e.retry if isinstance(e, SendFailed) else True
                row.error = str(e) if isinstance(e, SendFailed) else f"send raised {type(e).__name__}"
                if retry and row.attempts < MAX_ATTEMPTS:
                    row.status, row.send_after = "queued", now + timedelta(minutes=5 * 2 ** row.attempts)
                else:
                    row.status = "failed"
            s.commit()
            counts[row.status] = counts.get(row.status, 0) + 1
    return counts
