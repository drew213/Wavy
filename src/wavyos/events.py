"""Append-only event log with a per-tenant hash chain.

Each event's hash covers the previous event's hash and the event's own canonical body,
so a deleted or edited row breaks verify_chain(). It's tamper-evident, not tamper-proof:
someone with database access could rewrite the whole chain. Ship hashes off-box to anchor it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models as m
from .db import utcnow
from .scope import Scope

GENESIS = "0" * 64
RACE_RETRIES = 5


def _ts(dt) -> str:
    # SQLite hands back naive datetimes, Postgres aware ones: hash the same UTC wall time either way.
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def _canonical(e: m.Event) -> str:
    body = {
        "org_id": e.org_id, "client_id": e.client_id, "chain": e.chain, "seq": e.seq,
        "actor": e.actor, "actor_role": e.actor_role, "action": e.action,
        "object_kind": e.object_kind, "object_id": e.object_id, "summary": e.summary,
        "data": e.data, "created_at": _ts(e.created_at),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev: str, e: m.Event) -> str:
    return hashlib.sha256((prev + _canonical(e)).encode()).hexdigest()


def record(session: Session, scope: Scope, action: str, obj: Any, summary: str = "",
           data: Optional[dict] = None, object_kind: Optional[str] = None) -> m.Event:
    if hasattr(obj, "__tablename__") and getattr(obj, "id", None) is None:
        session.add(obj)
        session.flush()  # assign the id before it's written into the log
    chain = scope.client_id or "org"
    payload = json.loads(json.dumps(data or {}, default=str))  # stored exactly as it will be read back
    for attempt in range(RACE_RETRIES):
        seq, prev = _head(session, scope.org_id, chain)
        ev = m.Event(
            org_id=scope.org_id, client_id=scope.client_id, chain=chain, seq=seq,
            actor=scope.actor.ref, actor_role=",".join(sorted(scope.actor.roles)), action=action,
            object_kind=object_kind or getattr(obj, "__tablename__", "object"),
            object_id=str(getattr(obj, "id", obj)), summary=summary,
            data=payload, prev_hash=prev, created_at=utcnow().replace(microsecond=0),
        )
        ev.hash = _hash(prev, ev)
        try:
            with session.begin_nested():
                session.add(ev)
        except IntegrityError:  # another writer took this seq first; link onto its event instead
            if attempt == RACE_RETRIES - 1:
                raise
            continue
        return ev
    raise AssertionError("unreachable")


def _head(session: Session, org_id: str, chain: str) -> tuple[int, str]:
    """The next seq and the hash it links to."""
    last = session.execute(
        select(m.Event.seq, m.Event.hash).where(m.Event.org_id == org_id, m.Event.chain == chain)
        .order_by(m.Event.seq.desc()).limit(1)
    ).first()
    return (last[0] + 1, last[1]) if last else (1, GENESIS)


def verify_chain(session: Session, org_id: str, chain: str) -> tuple[bool, Optional[int]]:
    """(True, None) if intact; (False, seq) at the first broken link."""
    prev, expected = GENESIS, 1
    for e in session.scalars(select(m.Event).where(m.Event.org_id == org_id, m.Event.chain == chain).order_by(m.Event.seq)):
        if e.seq != expected or e.prev_hash != prev or _hash(prev, e) != e.hash:
            return False, e.seq
        prev, expected = e.hash, expected + 1
    return True, None


def count(session: Session, org_id: str, chain: str) -> int:
    return session.scalar(select(func.count()).select_from(m.Event).where(m.Event.org_id == org_id, m.Event.chain == chain)) or 0
