"""Klaviyo call budgets, per account per family per UTC day.

reporting: Klaviyo allows 225 reporting calls a day per account, and the brand's other apps spend from the
  same allowance. Wavy keeps itself well under that: a snapshot audit uses 5, and nothing may push an
  account past REPORT_DAILY_LIMIT.
events: pages of the events endpoint (24 months of orders). A first read of a large brand is a few hundred
  pages; weekly refreshes read only new orders. EVENTS_DAILY_PAGES stops a runaway re-read from hammering
  one account, and leaves room for a full read plus a retry in one day.
The count is committed before each call, so a crash can't hide spent calls.
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
EVENTS_DAILY_PAGES = 1500
LIMITS = {"reporting": REPORT_DAILY_LIMIT, "events": EVENTS_DAILY_PAGES}
NOUN = {"reporting": "reporting calls", "events": "event pages"}


class BudgetExceeded(Exception):
    """Today's allowance for this Klaviyo account and family is used up."""


def _day(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


def used(session: Session, account_id: str, now: Optional[datetime] = None, *, family: str = "reporting") -> int:
    return session.scalar(select(m.KlaviyoReportCall.count).where(
        m.KlaviyoReportCall.account_id == account_id, m.KlaviyoReportCall.family == family,
        m.KlaviyoReportCall.day == _day(now))) or 0


def spend(session: Session, account_id: str, n: int = 1, *, limit: Optional[int] = None,
          now: Optional[datetime] = None, family: str = "reporting") -> int:
    """Reserve n calls and commit, or raise BudgetExceeded. Returns today's total after spending."""
    if not account_id:
        raise ValueError("a Klaviyo account id is required to spend Klaviyo budget")
    if family not in LIMITS:
        raise ValueError(f"unknown Klaviyo budget family {family!r}")
    limit = LIMITS[family] if limit is None else limit
    day = _day(now)
    row = (m.KlaviyoReportCall.account_id == account_id, m.KlaviyoReportCall.family == family,
           m.KlaviyoReportCall.day == day)
    for _ in range(2):
        res = session.execute(update(m.KlaviyoReportCall).where(*row, m.KlaviyoReportCall.count + n <= limit)
                              .values(count=m.KlaviyoReportCall.count + n))
        if res.rowcount == 1:
            session.commit()
            return used(session, account_id, now, family=family)
        spent = used(session, account_id, now, family=family)
        if spent or n > limit:
            raise BudgetExceeded(f"Klaviyo {family} budget for today is used ({spent} of {limit} {NOUN[family]}); "
                                 "it resets at midnight UTC")
        try:
            with session.begin_nested():
                session.add(m.KlaviyoReportCall(account_id=account_id, family=family, day=day, count=n))
            session.commit()
            return n
        except IntegrityError:  # another worker created today's row first; add to it instead
            continue
    raise BudgetExceeded(f"could not reserve Klaviyo {family} budget")


def today(session: Session, account_ids: list[str], now: Optional[datetime] = None) -> dict[str, dict[str, int]]:
    """{account_id: {family: used}} for today, in one query."""
    out: dict[str, dict[str, int]] = {a: {f: 0 for f in LIMITS} for a in account_ids}
    if not account_ids:
        return out
    for acct, fam, n in session.execute(select(m.KlaviyoReportCall.account_id, m.KlaviyoReportCall.family,
                                               m.KlaviyoReportCall.count)
                                        .where(m.KlaviyoReportCall.account_id.in_(account_ids),
                                               m.KlaviyoReportCall.day == _day(now))):
        out.setdefault(acct, {})[fam] = n
    return out
