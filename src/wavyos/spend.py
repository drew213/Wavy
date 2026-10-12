"""Model spend per organization per UTC day.

Every paid model call (narrative drafting and critique, task executions) is charged here, and both stop
before crossing the org's daily cap: WAVYOS_ORG_LLM_DAILY_USD (default DEFAULT_ORG_DAILY_USD). The per-audit
cap (narrative) and the per-client daily budget (executions) still apply underneath; this is the ceiling
that holds however many brands are audited the same night.

Charges join the caller's transaction, so a job that rolls back un-charges with it; the call's own rows
(draft cost, execution cost) roll back the same way.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models as m

DEFAULT_ORG_DAILY_USD = 50.0


def daily_cap() -> float:
    try:
        return max(0.0, float(os.environ.get("WAVYOS_ORG_LLM_DAILY_USD") or DEFAULT_ORG_DAILY_USD))
    except ValueError:
        return DEFAULT_ORG_DAILY_USD


def _day(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


def used(session: Session, org_id: str, now: Optional[datetime] = None) -> float:
    return float(session.scalar(select(m.LlmSpend.cost_usd).where(m.LlmSpend.org_id == org_id,
                                                                  m.LlmSpend.day == _day(now))) or 0.0)


def left(session: Session, org_id: str, now: Optional[datetime] = None) -> float:
    return daily_cap() - used(session, org_id, now)


def charge(session: Session, org_id: str, usd: float, now: Optional[datetime] = None) -> None:
    """Add one call's cost to today's total for the org."""
    if not usd or usd <= 0:
        return
    day = _day(now)
    for _ in range(2):
        res = session.execute(update(m.LlmSpend).where(m.LlmSpend.org_id == org_id, m.LlmSpend.day == day)
                              .values(cost_usd=m.LlmSpend.cost_usd + usd, calls=m.LlmSpend.calls + 1))
        if res.rowcount == 1:
            return
        try:
            with session.begin_nested():
                session.add(m.LlmSpend(org_id=org_id, day=day, cost_usd=usd, calls=1))
            return
        except IntegrityError:  # another worker created today's row first
            continue


def cap_reason() -> str:
    return (f"Today's ${daily_cap():.2f} model spend cap for the agency was reached; it resets at midnight UTC "
            "(WAVYOS_ORG_LLM_DAILY_USD).")
