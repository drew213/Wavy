"""Durable onboard output, resumable order ingest, and live job progress.

Dossiers are versioned rows. Orders are upserted by (source, external id). A cursor per source
remembers the next page URL, and once a backfill finishes it remembers the newest order time so
the next run asks only for orders after that.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .connectors.shopify import ShopifyClient, ShopifyCohortData
from . import models as m
from .scope import Scope

SOURCE = "shopify"
KLAVIYO = "klaviyo"
KLAVIYO_PAGES_PER_RUN = 250
BACKFILL_DAYS = 365


def save_dossier(session: Session, scope: Scope, document: dict, job_run_id: Optional[str] = None) -> m.Dossier:
    client_id = scope.require_client()
    current = session.scalar(select(func.max(m.Dossier.version)).where(m.Dossier.client_id == client_id))
    row = m.Dossier(
        org_id=scope.org_id,
        client_id=client_id,
        version=int(current or 0) + 1,
        status=str(document.get("status") or "ACTIVE_EXECUTION"),
        document=document,
        job_run_id=job_run_id,
    )
    session.add(row)
    session.flush()
    return row


def latest_dossier(session: Session, client_id: str) -> Optional[m.Dossier]:
    return session.scalar(
        select(m.Dossier).where(m.Dossier.client_id == client_id).order_by(m.Dossier.version.desc()).limit(1)
    )


# ---------------------------------------------------------------- live progress

class Progress:
    """Stage-by-stage state for one job run. Every update commits, so readers see it mid-run.

    Committing also makes everything the job wrote so far durable, which is what lets an order
    import resume after a crash instead of starting over.
    """

    def __init__(self, session: Session, scope: Scope, run_id: str, stages: list[tuple[str, str]]):
        self.session = session
        self.row = session.scalar(select(m.JobProgress).where(m.JobProgress.job_run_id == run_id))
        if self.row is None:
            self.row = m.JobProgress(org_id=scope.org_id, client_id=scope.require_client(), job_run_id=run_id)
            session.add(self.row)
        self.state: dict[str, Any] = {
            "stages": [{"key": k, "label": label, "status": "pending", "detail": ""} for k, label in stages],
            "metrics": {}, "log": [],
        }
        self._save()

    def __call__(self, key: str, status: str, detail: str = "", **metrics: Any) -> None:
        for stage in self.state["stages"]:
            if stage["key"] == key:
                stage["status"] = status
                if detail:
                    stage["detail"] = detail
        self.state["metrics"].update(metrics)
        if detail:
            self.state["log"] = (self.state["log"] + [detail])[-12:]
        self._save()

    def _save(self) -> None:
        self.row.state = {**self.state, "stages": [dict(s) for s in self.state["stages"]],
                          "metrics": dict(self.state["metrics"]), "log": list(self.state["log"]),
                          "updated_at": datetime.now(timezone.utc).isoformat()}
        self.session.commit()


def progress_for(session: Session, run_id: str) -> Optional[dict]:
    row = session.scalar(select(m.JobProgress).where(m.JobProgress.job_run_id == run_id))
    return row.state if row is not None else None


# ---------------------------------------------------------------- order ingest

def ingest_shopify(session: Session, scope: Scope, client: ShopifyClient, max_pages: int = 20) -> m.IngestCursor:
    """Pull up to max_pages and upsert them. A later call continues at the saved URL or watermark."""
    cursor = _cursor(session, scope, SOURCE)
    start = cursor.next_url
    watermark = None
    if not start and cursor.watermark is not None:
        watermark = _z(cursor.watermark)
    for orders, nxt in client.pull_order_pages(start_url=start, max_pages=max_pages, created_at_min=watermark):
        cursor.orders_stored += _upsert_orders(session, scope, SOURCE, [_shopify_row(o) for o in orders])
        cursor.pages_pulled += 1
        _advance(session, scope, cursor, SOURCE, nxt)
    return cursor


def ingest_klaviyo_orders(session: Session, scope: Scope, client, metric_id: str, *,
                          max_pages: Optional[int] = None, now: Optional[datetime] = None,
                          on_page: Optional[Callable[[m.IngestCursor, int], None]] = None) -> tuple[m.IngestCursor, bool]:
    """Import Placed Order events, oldest first. Returns (cursor, finished).

    finished is False when the page budget ran out first; the cursor's next_url picks up from there.
    on_page(cursor, added) runs after every page (the onboarding job uses it to report progress).
    """
    now = now or datetime.now(timezone.utc)
    max_pages = max_pages or KLAVIYO_PAGES_PER_RUN
    cursor = _cursor(session, scope, KLAVIYO)
    since = cursor.watermark if cursor.watermark is not None else now - timedelta(days=BACKFILL_DAYS)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    finished = cursor.next_url is None
    pages = 0
    for events, nxt in client.event_pages(metric_id, since, now, start_url=cursor.next_url, max_pages=max_pages):
        added = _upsert_orders(session, scope, KLAVIYO, [r for r in (_klaviyo_row(e) for e in events) if r])
        cursor.orders_stored += added
        cursor.pages_pulled += 1
        pages += 1
        _advance(session, scope, cursor, KLAVIYO, nxt)
        finished = nxt is None
        if on_page is not None:
            on_page(cursor, added)
    if pages == 0:
        finished = True
    return cursor, finished


def count_orders(session: Session, scope: Scope, source: str) -> int:
    return int(session.scalar(select(func.count()).select_from(m.CommerceOrder).where(
        m.CommerceOrder.client_id == scope.require_client(), m.CommerceOrder.source == source)) or 0)


def cohort_from_orders(session: Session, scope: Scope, label: str, source: str = SOURCE) -> Optional[ShopifyCohortData]:
    client_id = scope.require_client()
    rows = session.execute(select(
        m.CommerceOrder.customer_key, m.CommerceOrder.ordered_at, m.CommerceOrder.total,
        m.CommerceOrder.gateway_sku, m.CommerceOrder.refunded, m.CommerceOrder.currency,
    ).where(m.CommerceOrder.client_id == client_id, m.CommerceOrder.source == source)
     .order_by(m.CommerceOrder.ordered_at)).all()
    if not rows:
        return None
    gmv = 0.0
    refunds = 0
    by_customer: dict[str, list] = {}
    first_sku: dict[str, int] = {}
    repeat_sku: dict[str, int] = {}
    for customer_key, ordered_at, total, sku, refunded, _currency in rows:
        gmv += float(total or 0)
        refunds += 1 if refunded else 0
        history = by_customer.setdefault(customer_key, [])
        if sku:
            bucket = repeat_sku if history else first_sku
            bucket[sku] = bucket.get(sku, 0) + 1
        history.append(ordered_at)
    customers = len(by_customer)
    repeats = 0
    gaps = []
    for history in by_customer.values():
        if len(history) < 2:
            continue
        repeats += 1
        gaps.append((history[1] - history[0]).total_seconds() / 86400)
    gaps.sort()
    median = int(round(gaps[len(gaps) // 2])) if gaps else 0

    def top(counts: dict[str, int]) -> list[dict]:
        return [{"name": name, "units": n} for name, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:3]]
    return ShopifyCohortData(
        store_domain=label,
        currency=str(rows[0][5] or "USD"),
        total_orders_12m=len(rows),
        total_unique_customers_12m=customers,
        lifetime_gmv_12m=round(gmv, 2),
        blended_aov=round(gmv / len(rows), 2) if rows else 0.0,
        repeat_customer_count=repeats,
        repeat_purchase_rate=round(repeats / customers, 3) if customers else 0.0,
        median_days_to_second_order=median,
        top_gateway_skus=top(first_sku),
        top_repeat_skus=top(repeat_sku) or top(first_sku),
        refund_rate_pct=round(100 * refunds / len(rows), 2) if rows else 0.0,
    )


def _cursor(session: Session, scope: Scope, source: str) -> m.IngestCursor:
    client_id = scope.require_client()
    row = session.scalar(select(m.IngestCursor).where(
        m.IngestCursor.client_id == client_id, m.IngestCursor.source == source))
    if row is None:
        row = m.IngestCursor(org_id=scope.org_id, client_id=client_id, source=source, pages_pulled=0, orders_stored=0)
        session.add(row)
        session.flush()
    return row


def _advance(session: Session, scope: Scope, cursor: m.IngestCursor, source: str, nxt: Optional[str]) -> None:
    cursor.next_url = nxt
    if nxt is None:
        cursor.watermark = session.scalar(select(func.max(m.CommerceOrder.ordered_at)).where(
            m.CommerceOrder.client_id == scope.require_client(), m.CommerceOrder.source == source))
    session.flush()


def _upsert_orders(session: Session, scope: Scope, source: str, rows: list[dict]) -> int:
    client_id = scope.require_client()
    ids = [r["external_id"] for r in rows]
    if not ids:
        return 0
    known = set(session.scalars(select(m.CommerceOrder.external_id).where(
        m.CommerceOrder.client_id == client_id, m.CommerceOrder.source == source,
        m.CommerceOrder.external_id.in_(ids))))
    added = 0
    for r in rows:
        if r["external_id"] in known:
            continue
        known.add(r["external_id"])
        session.add(m.CommerceOrder(org_id=scope.org_id, client_id=client_id, source=source, **r))
        added += 1
    session.flush()
    return added


def _shopify_row(order: dict) -> Optional[dict]:
    external_id = str(order.get("id") or "")
    if not external_id:
        return None
    customer = order.get("customer") or {}
    items = order.get("line_items") or []
    first = items[0] if items else {}
    return {
        "external_id": external_id,
        "customer_key": str(customer.get("id") or order.get("email") or external_id),
        "ordered_at": _parse_dt(order.get("created_at")),
        "total": float(order.get("total_price") or 0),
        "currency": str(order.get("currency") or "USD"),
        "gateway_sku": str(first.get("title") or first.get("name") or "")[:300],
        "refunded": str(order.get("financial_status") or "") == "refunded",
    }


def _klaviyo_row(event: dict) -> Optional[dict]:
    """One Placed Order event as an order. The profile id is the customer; no email or name is kept."""
    attrs = event.get("attributes") or {}
    props = attrs.get("event_properties") or {}
    external_id = str(props.get("$event_id") or event.get("id") or "")[:64]
    if not external_id:
        return None
    profile = (((event.get("relationships") or {}).get("profile") or {}).get("data") or {}).get("id")
    names = props.get("ItemNames") or [i.get("ProductName") for i in (props.get("Items") or []) if isinstance(i, dict)]
    first = next((str(n) for n in names if n), "")
    try:
        total = float(props.get("$value") if props.get("$value") is not None else attrs.get("value") or 0)
    except (TypeError, ValueError):
        total = 0.0
    return {
        "external_id": external_id,
        "customer_key": str(profile or external_id)[:120],
        "ordered_at": _parse_dt(attrs.get("datetime") or attrs.get("timestamp")),
        "total": total,
        "currency": str(props.get("$currency_code") or props.get("Currency") or "USD")[:8],
        "gateway_sku": first[:300],
        "refunded": False,
    }


def _z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
