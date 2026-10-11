"""Klaviyo reporting-endpoint budget, per account per UTC day.

Klaviyo allows 225 reporting calls a day per account, and the brand's other apps spend from the same
allowance. Wavy keeps itself well under that: a snapshot audit uses 5, and nothing may push an account
past REPORT_DAILY_LIMIT. The count is committed before each call, so a crash can't hide spent calls.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models as m

REPORT_DAILY_LIMIT = 40
SNAPSHOT_REPORT_CALLS = 5


class BudgetExceeded(Exception):
    """Today's reporting allowance for this Klaviyo account is used up."""


def _day(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


def used(session: Session, account_id: str, now: Optional[datetime] = None) -> int:
    return session.scalar(select(m.KlaviyoReportCall.count).where(
        m.KlaviyoReportCall.account_id == account_id, m.KlaviyoReportCall.day == _day(now))) or 0


def spend(session: Session, account_id: str, n: int = 1, *, limit: int = REPORT_DAILY_LIMIT,
          now: Optional[datetime] = None) -> int:
    """Reserve n calls and commit, or raise BudgetExceeded. Returns today's total after spending."""
    if not account_id:
        raise ValueError("a Klaviyo account id is required to spend reporting budget")
    day = _day(now)
    for _ in range(2):
        res = session.execute(update(m.KlaviyoReportCall)
                              .where(m.KlaviyoReportCall.account_id == account_id, m.KlaviyoReportCall.day == day,
                                     m.KlaviyoReportCall.count + n <= limit)
                              .values(count=m.KlaviyoReportCall.count + n))
        if res.rowcount == 1:
            session.commit()
            return used(session, account_id, now)
        if used(session, account_id, now) or n > limit:
            raise BudgetExceeded(f"Klaviyo reporting budget for today is used ({used(session, account_id, now)} of "
                                 f"{limit}); it resets at midnight UTC")
        try:
            with session.begin_nested():
                session.add(m.KlaviyoReportCall(account_id=account_id, day=day, count=n))
            session.commit()
            return n
        except IntegrityError:  # another worker created today's row first; add to it instead
            continue
    raise BudgetExceeded("could not reserve Klaviyo reporting budget")
