"""Rate limits for the public account endpoints (sign-up, sign-in), kept in the database.

Every web process shares the same counts, so adding instances doesn't multiply the allowance.
The window slides: the previous window's count is weighted by how much of it still overlaps,
which needs two rows per key instead of one row per attempt. Two requests racing can both get
through at the edge of the limit; that is fine for slowing down password guessing.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models as m
from .db import utcnow


class Limiter:
    def __init__(self, name: str, limit: int, window_s: int):
        self.name, self.limit, self.window = name, limit, window_s

    def allow(self, session: Session, key: str, now: Optional[float] = None) -> bool:
        """Record an attempt for key; False if it is over the limit. Commits the session."""
        now = time.time() if now is None else now
        h = hashlib.sha256(f"{self.name}|{key}".encode()).hexdigest()
        bucket = int(now // self.window)
        counts = dict(session.execute(select(m.RateLimit.bucket, m.RateLimit.count)
                                      .where(m.RateLimit.key == h, m.RateLimit.bucket.in_((bucket - 1, bucket)))).all())
        overlap = 1 - (now % self.window) / self.window
        if counts.get(bucket, 0) + counts.get(bucket - 1, 0) * overlap >= self.limit:
            return False
        expires = datetime.fromtimestamp((bucket + 2) * self.window, timezone.utc)
        for _ in range(3):
            if session.execute(update(m.RateLimit).where(m.RateLimit.key == h, m.RateLimit.bucket == bucket)
                               .values(count=m.RateLimit.count + 1)).rowcount:
                break
            try:
                with session.begin_nested():
                    session.add(m.RateLimit(key=h, bucket=bucket, count=1, expires_at=expires))
                break
            except IntegrityError:  # another request inserted it first; add to theirs
                continue
        session.commit()  # counted even if the request then fails and rolls back
        return True


def prune(session: Session) -> int:
    return session.execute(delete(m.RateLimit).where(m.RateLimit.expires_at < utcnow())).rowcount
