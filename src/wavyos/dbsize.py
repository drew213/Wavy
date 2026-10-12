"""Database storage per brand, for /healthz, the staff scale view and the move-to-paid trigger.

On Postgres each brand's size is the sum of pg_column_size over its rows in every table with a client_id
(orders, artifacts, facts, bundles, drafts, events...), and the database total is pg_database_size. Other
dialects (SQLite in development) report row counts only. Measuring scans those tables, so callers use
cached(), which measures at most once per TTL per process.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from . import models as m
from .db import Base

log = logging.getLogger("wavyos.dbsize")

FREE_PLAN_MB = 512          # Neon free
WARN_SHARE = 0.7
TTL_SECONDS = 15 * 60
MB = 1024 * 1024


def _tables() -> list:
    return [t for t in Base.metadata.sorted_tables if "client_id" in t.c and t.name != "clients"]


def measure(session: Session) -> dict:
    """{dialect, database_mb, free_plan_mb, used_share, brands: [{client_id, slug, name, mb, rows}], largest_mb}."""
    pg = session.get_bind().dialect.name == "postgresql"
    per: dict[str, dict] = {}
    for t in _tables():
        cols = [t.c.client_id, func.count()]
        if pg:
            cols.append(func.sum(func.pg_column_size(text(f'"{t.name}".*'))))
        for row in session.execute(select(*cols).select_from(t).group_by(t.c.client_id)):
            if row[0] is None:
                continue
            b = per.setdefault(row[0], {"rows": 0, "bytes": 0})
            b["rows"] += int(row[1] or 0)
            if pg:
                b["bytes"] += int(row[2] or 0)
    clients = {c.id: c for c in session.scalars(select(m.Client))}
    brands = []
    for cid, b in per.items():
        c = clients.get(cid)
        brands.append({"client_id": cid, "slug": c.slug if c else "", "name": c.name if c else "",
                       "org_id": c.org_id if c else None, "rows": b["rows"],
                       "mb": round(b["bytes"] / MB, 2) if pg else None})
    brands.sort(key=lambda r: (-(r["mb"] or 0), -r["rows"]))
    if pg:
        total = session.scalar(text("select pg_database_size(current_database())")) or 0
    else:
        try:
            total = (session.scalar(text("pragma page_count")) or 0) * (session.scalar(text("pragma page_size")) or 0)
        except Exception:
            total = 0
    db_mb = round(total / MB, 2)
    return {"dialect": session.get_bind().dialect.name, "database_mb": db_mb, "free_plan_mb": FREE_PLAN_MB,
            "used_share": round(db_mb / FREE_PLAN_MB, 3), "brands": brands,
            "largest_mb": brands[0]["mb"] if brands and pg else None, "measured_at": time.time()}


def summary(report: dict) -> dict:
    """What /healthz shows: totals only, never brand names (it's public)."""
    if "error" in report:
        return {"error": report["error"]}
    return {"database_mb": report["database_mb"], "free_plan_mb": report["free_plan_mb"],
            "used_share": report["used_share"], "brands": len(report["brands"]),
            "largest_brand_mb": report["largest_mb"], "warn": report["used_share"] >= WARN_SHARE}


def log_report(report: dict) -> None:
    log.info("storage database_mb=%s used_share=%s brands=%s", report["database_mb"], report["used_share"],
             len(report["brands"]))
    for b in report["brands"]:
        log.info("storage brand=%s mb=%s rows=%s", b["slug"] or b["client_id"], b["mb"], b["rows"])
    if report["used_share"] >= WARN_SHARE:
        log.warning("storage is at %.0f%% of the %s MB free plan; see docs/SCALING.md",
                    report["used_share"] * 100, report["free_plan_mb"])


_lock = threading.Lock()


def cached(factory: sessionmaker[Session], *, ttl: float = TTL_SECONDS, force: bool = False) -> dict:
    """The last measurement if it's fresher than ttl, else a new one (logged per brand)."""
    with _lock:
        hit = getattr(factory, "_wavyos_storage", None)
        if hit and not force and time.time() - hit["measured_at"] < ttl:
            return hit
        try:
            with factory() as s:
                report = measure(s)
        except Exception as e:  # health must answer even when the database can't be measured
            log.warning("storage measurement failed: %s", type(e).__name__)
            return {"error": "storage could not be measured", "measured_at": time.time(), "brands": []}
        log_report(report)
        factory._wavyos_storage = report
        return report


def for_clients(report: dict, client_ids: Optional[set[str]]) -> dict:
    """The report limited to brands the caller can see."""
    if client_ids is None:
        return report
    return {**report, "brands": [b for b in report.get("brands", []) if b["client_id"] in client_ids]}
