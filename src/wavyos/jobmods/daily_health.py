"""account.daily_health: catch a flow that stopped sending before the client does.

Each run reads every flow's status from each active Klaviyo connection (read-only) and compares it
with what the last run saw. Statuses are kept as MetricSnapshot rows, one row per stretch of days a
flow held one status (period_start = first seen, period_end = last seen), so the history stays small
and every insight can point at the exact rows it came from.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from .. import connections, operations
from .. import models as m
from ..connectors.klaviyo import KlaviyoError
from ..db import new_id
from ..jobs import JobContext, JobOutcome, job
from ._util import plural, today_for

KEY = "account.daily_health"
SOURCE = "klaviyo"
PREFIX = "flow_status:"
STOPPED = frozenset({"draft", "manual", "paused", "archived", "missing"})  # a live flow in one of these has stopped sending
ERRORED = frozenset({"error", "errored", "failed", "disabled"})


def flow_status(resource: dict) -> str:
    attrs = resource.get("attributes") or {}
    if attrs.get("archived"):
        return "archived"
    return str(attrs.get("status") or "unknown").strip().lower()


def latest_statuses(ctx: JobContext) -> dict[str, m.MetricSnapshot]:
    """The most recent status row for every flow this client has had recorded."""
    rows = ctx.session.scalars(select(m.MetricSnapshot).where(
        m.MetricSnapshot.org_id == ctx.scope.org_id, m.MetricSnapshot.client_id == ctx.scope.require_client(),
        m.MetricSnapshot.source == SOURCE, m.MetricSnapshot.metric_key.like(PREFIX + "%"),
    ).order_by(m.MetricSnapshot.period_end, m.MetricSnapshot.created_at))
    return {r.metric_key[len(PREFIX):]: r for r in rows}


def _record(ctx: JobContext, today, conn: m.Connection, flow_id: str, status: str, name: str,
            prev: Optional[m.MetricSnapshot]) -> m.MetricSnapshot:
    payload = {"status": status, "name": name, "connection_id": conn.id,
               "account_id": conn.external_account_id, "run_id": ctx.run.id}
    if prev is not None and (prev.payload or {}).get("status") == status:
        prev.period_end = max(prev.period_end, today)
        prev.payload = {**prev.payload, **payload}  # reassign so the JSON column is marked dirty
        return prev
    snap = m.MetricSnapshot(id=new_id(), org_id=ctx.scope.org_id, client_id=ctx.scope.client_id, source=SOURCE,
                            metric_key=PREFIX + flow_id, period_start=today, period_end=today,
                            value=1.0 if status == "live" else 0.0, payload=payload)
    ctx.session.add(snap)
    return snap


def _flag_change(ctx, flow_id, name, before, after, prev, snap, today) -> Optional[str]:
    if before == "live" and after in STOPPED:
        title = f"Flow \"{name}\" stopped sending: live to {after}"
        severity = "high"
        summary = (f"It was live on {prev.period_end.isoformat()} and is {after} on {today.isoformat()}. "
                   "Check whether someone changed it on purpose; if not, it needs to go back live (with approval).")
    elif after in ERRORED and before != after:
        title = f"Flow \"{name}\" is reporting {after}"
        severity = "high"
        summary = f"It was {before or 'not yet seen'} and is {after} on {today.isoformat()}."
    else:
        return None
    evidence = [{"kind": "metric_snapshot", "ref": f"metric_snapshot:{snap.id}",
                 "detail": f"flow {flow_id} {after} on {today.isoformat()}"},
                {"kind": "klaviyo_flow", "ref": f"klaviyo:flow:{flow_id}", "detail": name}]
    if prev is not None:
        evidence.insert(0, {"kind": "metric_snapshot", "ref": f"metric_snapshot:{prev.id}",
                            "detail": f"flow {flow_id} {before} {prev.period_start.isoformat()} to "
                                      f"{prev.period_end.isoformat()}"})
    ins, created = operations.create_insight(
        ctx.session, ctx.scope, area="flow_audit", title=title, summary=summary, severity=severity,
        evidence=evidence, source_kind="monitor", source_ref=f"job_run:{ctx.run.id}",
        dedupe_key=f"flow_status:{flow_id}:{after}")
    if created:
        ctx.note("insights", ins.id)
        return ins.id
    return None


def _connection_insight(ctx, conn, title, detail, dedupe, severity="high"):
    ins, created = operations.create_insight(
        ctx.session, ctx.scope, area="data_infrastructure", title=title, severity=severity,
        summary="The daily health check couldn't read this account, so flow changes there aren't being watched.",
        evidence=[{"kind": "connection", "ref": f"connection:{conn.id}", "detail": detail}],
        source_kind="monitor", source_ref=f"job_run:{ctx.run.id}", dedupe_key=dedupe)
    if created:
        ctx.note("insights", ins.id)


@job(KEY, "Daily account health", "daily", "analyst", area="flow_audit", family="klaviyo",
     checks="Every flow in each connected Klaviyo account: is it still in the state it was in yesterday? "
            "Has a live flow gone to draft, manual, paused or archived, disappeared, or started reporting an error? "
            "Does the key still open the account we connected?",
     action="Records each flow's status, and opens one insight with evidence per flow that stopped or errored "
            "(never two for the same change). Reads only; it never touches a flow.",
     completion="Done when every active connection was read and compared, or the run says why not "
                "(no Klaviyo connection, baseline just recorded, nothing changed).")
def daily_health(ctx: JobContext) -> JobOutcome:
    s, scope = ctx.session, ctx.scope
    conns = connections.active(s, scope, "klaviyo")
    if not conns:
        return JobOutcome("looked for connected accounts", no_action_reason="no Klaviyo connection")
    vault, make_client = ctx.services["vault"], ctx.services["klaviyo_factory"]
    today = today_for(ctx)
    previous = latest_statuses(ctx)
    checked = baselined = changed = unreadable = 0
    for conn in conns:
        try:
            with connections.secret(s, scope, vault, conn.id, purpose="daily health") as key:
                client = make_client(key)
                try:
                    account = client.account()
                    flows = client.flows()
                finally:
                    close = getattr(client, "close", None)
                    if callable(close):
                        close()
        except KlaviyoError as e:
            _connection_insight(ctx, conn, f"Klaviyo account {conn.external_account_id} couldn't be read", str(e),
                                f"klaviyo_connection_error:{conn.id}")
            unreadable += 1
            continue
        if account.get("id") != conn.external_account_id:
            _connection_insight(ctx, conn, f"Klaviyo key for {conn.external_account_id} opens a different account",
                                f"expected {conn.external_account_id}, key opened {account.get('id')}",
                                f"klaviyo_account_mismatch:{conn.id}", severity="critical")
            unreadable += 1
            continue
        mine = {fid: snap for fid, snap in previous.items() if (snap.payload or {}).get("connection_id") == conn.id}
        first_run = not mine
        seen = set()
        for f in flows:
            fid = str(f.get("id"))
            seen.add(fid)
            status, name = flow_status(f), (f.get("attributes") or {}).get("name") or fid
            prev = mine.get(fid)
            before = (prev.payload or {}).get("status") if prev is not None else None
            snap = _record(ctx, today, conn, fid, status, name, prev)
            checked += 1
            if not first_run and before != status and _flag_change(ctx, fid, name, before, status, prev, snap, today):
                changed += 1
        for fid, prev in mine.items():  # a flow we saw before that Klaviyo no longer lists
            before = (prev.payload or {}).get("status")
            if fid in seen or before == "missing":
                continue
            name = (prev.payload or {}).get("name") or fid
            snap = _record(ctx, today, conn, fid, "missing", name, prev)
            if _flag_change(ctx, fid, name, before, "missing", prev, snap, today):
                changed += 1
        baselined += first_run
    summary = f"checked {plural(checked, 'flow')} across {plural(len(conns), 'Klaviyo connection')}"
    if unreadable:
        summary += f"; {plural(unreadable, 'connection')} couldn't be read"
    if changed:
        return JobOutcome(f"{summary}; {plural(changed, 'flow')} stopped or errored")
    reasons = []
    if baselined:
        reasons.append("baseline recorded")
    if unreadable:
        reasons.append("unreadable connections are flagged in open insights")
    if baselined + unreadable < len(conns):
        reasons.append("no flow stopped or errored since the last run")
    return JobOutcome(summary, no_action_reason="; ".join(reasons))
