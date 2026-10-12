"""The morning strategist queue: what the overnight batch changed, what's ready to approve, what's blocked.

Everything is read from rows the loop already writes (workspace versions, changes facts, narrative
drafts, outcomes, failed runs and jobs), with one grouped query per table, so the queue costs the same
for 5 brands or 500.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from .outcomes import view as outcome_view

HEADLINE_FACTS = ("changes.score_delta", "changes.checks_regressed", "changes.checks_improved",
                  "changes.opportunities_new", "changes.opportunities_resolved", "changes.days_between")
TOP_MOVERS = 3
DUE_SOON = timedelta(days=7)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def queue(session: Session, client_ids: list[str], *, hours: int = 24, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    clients = {c.id: c for c in session.scalars(select(m.Client).where(m.Client.id.in_(client_ids)))}
    who = lambda cid: {"client": clients[cid].slug, "client_name": clients[cid].name}  # noqa: E731

    bundles = list(session.scalars(select(m.WorkspaceBundle).where(
        m.WorkspaceBundle.client_id.in_(client_ids), m.WorkspaceBundle.created_at >= since)
        .order_by(m.WorkspaceBundle.created_at.desc())))
    run_ids = [b.run_id for b in bundles]
    facts: dict[str, dict[str, m.AuditFact]] = {}
    for f in session.scalars(select(m.AuditFact).where(m.AuditFact.run_id.in_(run_ids), m.AuditFact.module == "changes")):
        facts.setdefault(f.run_id, {})[f.fact_id] = f
    runs = {r.id: r for r in session.scalars(select(m.AuditRun).where(m.AuditRun.id.in_(run_ids)))}
    changed = []
    for b in bundles:
        fs, run = facts.get(b.run_id, {}), runs.get(b.run_id)
        movers = sorted((f for fid, f in fs.items() if fid.endswith(".pct_change") and (f.value or {}).get("v") is not None),
                        key=lambda f: -abs(f.value["v"]))[:TOP_MOVERS]
        changed.append({**who(b.client_id), "version": b.version, "run_id": b.run_id, "status": b.status,
                        "refresh": ((run.context or {}).get("refresh") if run else None) or "first audit",
                        "published_at": _iso(b.created_at),
                        "first_audit": not fs,
                        **{fid.split(".", 1)[1]: (fs[fid].value or {}).get("v") for fid in HEADLINE_FACTS if fid in fs},
                        "movers": [{"label": f.label.replace(", % change since previous audit", ""),
                                    "pct_change": f.value["v"], "fact": f.fact_id} for f in movers]})

    drafts = session.scalars(select(m.NarrativeDraft).where(
        m.NarrativeDraft.client_id.in_(client_ids), m.NarrativeDraft.status.in_(("draft", "needs_rewrite")))
        .order_by(m.NarrativeDraft.created_at))
    ready, rewrite = [], []
    for d in drafts:
        item = {**who(d.client_id), "id": d.id, "section": d.section, "score": d.score, "rounds": d.rounds,
                "headline": (d.payload or {}).get("headline"), "reason": d.reason, "since": _iso(d.updated_at or d.created_at)}
        (ready if d.status == "draft" else rewrite).append(item)

    blocked = []
    for r in session.scalars(select(m.AuditRun).where(m.AuditRun.client_id.in_(client_ids), m.AuditRun.status == "failed",
                                                      m.AuditRun.finished_at >= since)):
        blocked.append({**who(r.client_id), "kind": "audit_failed", "id": r.id, "reason": r.error or "the audit run failed",
                        "at": _iso(r.finished_at)})
    for j in session.scalars(select(m.JobRun).where(m.JobRun.client_id.in_(client_ids), m.JobRun.status == "failed",
                                                    m.JobRun.finished_at >= since)):
        blocked.append({**who(j.client_id), "kind": "job_failed", "id": j.id, "job": j.job_key,
                        "reason": (j.error or "").split("\n")[0][:300], "at": _iso(j.finished_at)})
    for j in session.scalars(select(m.JobRun).where(
            m.JobRun.client_id.in_(client_ids), m.JobRun.status == "no_action", m.JobRun.finished_at >= since,
            m.JobRun.job_key.in_(("audit.refresh", "monthly.report", "narrative.draft")))):
        if any(w in (j.summary or "") for w in ("reporting calls", "spend cap", "No model provider", "no active Klaviyo")):
            blocked.append({**who(j.client_id), "kind": "deferred", "id": j.id, "job": j.job_key,
                            "reason": j.summary, "at": _iso(j.finished_at)})
    for d in session.scalars(select(m.NarrativeDraft).where(
            m.NarrativeDraft.client_id.in_(client_ids), m.NarrativeDraft.status == "skipped",
            m.NarrativeDraft.updated_at >= since)):
        blocked.append({**who(d.client_id), "kind": "draft_skipped", "id": d.id, "section": d.section,
                        "reason": d.reason, "at": _iso(d.updated_at)})
    blocked.sort(key=lambda b: b["at"] or "", reverse=True)

    out_rows = list(session.scalars(select(m.OpportunityOutcome).where(m.OpportunityOutcome.client_id.in_(client_ids))))
    decided = [{**who(o.client_id), **outcome_view(o)} for o in out_rows
               if o.decided_at is not None and _iso(o.decided_at) >= since.isoformat()]
    due = [{**who(o.client_id), **outcome_view(o)} for o in out_rows
           if o.status == "tracking" and _iso(o.due_at) <= (now + DUE_SOON).isoformat()]
    return {"since": since.isoformat(), "now": now.isoformat(), "hours": hours, "changed": changed,
            "drafts_ready": ready, "needs_rewrite": rewrite, "blocked": blocked, "outcomes_decided": decided,
            "outcomes_due": sorted(due, key=lambda o: o["due_at"] or ""),
            "counts": {"changed": len(changed), "drafts_ready": len(ready), "needs_rewrite": len(rewrite),
                       "blocked": len(blocked), "outcomes_decided": len(decided), "outcomes_due": len(due)}}
