"""Measured impact of live fixes, and each brand's progress toward its first $10K in 30 days.

A fix is an approved opportunity (opportunity_outcomes) that has gone live (shipped_at). Its targets are the
flows, or single flow messages, it changes. Once it has been live MIN_LIVE, impact.measure reads Klaviyo's
flow values for two windows, up to 28 days before the change and the time since, and writes a reading:

  before_after   the targets' revenue per recipient before vs since, scaled by the trend of every flow the
                 fix didn't touch: lift = (after rate - before rate x trend) x recipients since.
  attributed_only  the targets weren't sending before (a new flow): there is no before to compare, so only
                 attributed revenue is shown; a holdout split measures these once Wavy builds flows itself.

Only measured lift counts toward the goal. Attributed revenue is shown beside it, never added to it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import events
from . import models as m
from .db import utcnow
from .policy import LEADS
from .scope import InvalidState, NotPermitted, Scope

GOAL = 10_000.0
WINDOW_DAYS = 30
MIN_LIVE = timedelta(days=7)
BEFORE = timedelta(days=28)
MIN_RECIPIENTS = 500
TREND_BOUNDS = (0.5, 2.0)
MAX_SHIP_DATES = 6           # distinct ship dates read per brand per day (two reporting calls each)
SHIPPERS = LEADS | {"strategist"}

# journey_map findings -> the per-message suggestion rule that marks the messages they change
RULE_FOR_FINDING = {
    "journey_map.weak_steps": "weak_step", "journey_map.slow_first_touch": "slow_first_touch",
    "journey_map.switched_off_steps": "switched_off", "journey_map.discount_first_touch": "discount_first",
    "journey_map.missing_preview_text": "no_preview", "journey_map.duplicate_subjects": "duplicate_subject",
}


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- what a fix touches

def lever_kinds(lever: Optional[str]) -> set:
    from .audit import optimal_journey
    if not lever:
        return set()
    return {k for mo in optimal_journey.library()["moments"] if mo.get("lever") == lever for k in mo.get("kinds") or []}


def targets_for(card: dict, tables: dict) -> list[dict]:
    """The live flows (or flow messages) a card's fix changes, from the run's journey map."""
    from .analysis.journey_map import walk_messages
    flows = [f for f in ((tables.get("journey_map") or {}).get("flows") or []) if f.get("live")]
    rule = RULE_FOR_FINDING.get(card.get("finding_id") or "")
    if rule:
        return [{"flow_id": f["id"], "message_id": x["message_id"], "name": f"{f['name']} / {x.get('name') or 'step ' + str(x['n'])}"}
                for f in flows for x in walk_messages(f.get("tree") or [])
                if x.get("message_id") and any(s.get("rule") == rule for s in x.get("suggestions") or [])]
    kinds = lever_kinds((card.get("sizing") or {}).get("lever"))
    return [{"flow_id": f["id"], "name": f["name"]} for f in flows if f.get("kind") in kinds]


def mark_shipped(session: Session, scope: Scope, outcome_id: str, *, at: Optional[datetime] = None,
                 targets: Optional[list[dict]] = None, by: str = "") -> m.OpportunityOutcome:
    """Staff record that a fix went live (the Klaviyo write connector will do this itself)."""
    if not scope.actor.is_human or not (scope.actor.roles & SHIPPERS):
        raise NotPermitted("marking a fix live needs one of: " + ", ".join(sorted(SHIPPERS)))
    o = session.get(m.OpportunityOutcome, outcome_id)
    if o is None or o.client_id != scope.require_client():
        raise InvalidState("that fix isn't on this brand")
    if o.status == "withdrawn":
        raise InvalidState("this fix was withdrawn")
    o.shipped_at, o.shipped_by = _aware(at) or utcnow(), by or scope.actor.ref
    if targets is not None:
        o.targets = [t for t in targets if t.get("flow_id")]
    o.updated_at = utcnow()
    events.record(session, scope, "opportunity.shipped", o, f"{o.title} went live",
                  {"shipped_at": o.shipped_at.isoformat(), "targets": len(o.targets or [])})
    return o


def detect_shipped(session: Session, client_id: str, result: dict, run: m.AuditRun) -> list[m.OpportunityOutcome]:
    """Approved fixes whose target messages changed since the last audit are live: the change landed before this
    run started, so the run's start is used as the ship date (a late date only makes the measured lift smaller)."""
    rows = (((result.get("modules") or {}).get("journey_map") or {}).get("tables") or {}).get("messages") or []
    changed = {r["message_id"] for r in rows if r.get("message_id") and (r.get("since_last") or {})
               and any((r["since_last"] or {}).get(k) for k in ("subject_changed", "preview_changed", "html_changed"))}
    out = []
    if not changed:
        return out
    for o in session.scalars(select(m.OpportunityOutcome).where(m.OpportunityOutcome.client_id == client_id,
                                                                m.OpportunityOutcome.shipped_at.is_(None),
                                                                m.OpportunityOutcome.status == "tracking")):
        hit = [t for t in o.targets or [] if t.get("message_id") in changed]
        if hit:
            o.shipped_at, o.shipped_by, o.updated_at = _aware(run.created_at), "detected: the next audit saw it change", utcnow()
            out.append(o)
    return out


# ---------------------------------------------------------------- the math

def _sum(rows: dict, keep) -> dict:
    rec = rev = 0.0
    for (fid, mid), v in rows.items():
        if keep(fid, mid):
            rec += v.get("recipients") or 0
            rev += v.get("revenue") or 0
    return {"recipients": rec, "revenue": round(rev, 2), "rate": (rev / rec) if rec else None}


def compute(targets: list[dict], before: dict, after: dict) -> dict:
    """One reading from two windows of flow values keyed (flow_id, message_id) -> {recipients, revenue}."""
    whole = {t["flow_id"] for t in targets if not t.get("message_id")}
    msgs = {(t["flow_id"], t["message_id"]) for t in targets if t.get("message_id")}
    touched = whole | {f for f, _ in msgs}

    def is_target(fid, mid):
        return fid in whole or (fid, mid) in msgs

    b, a = _sum(before, is_target), _sum(after, is_target)
    ob, oa = _sum(before, lambda f, _: f not in touched), _sum(after, lambda f, _: f not in touched)
    out = {"before": b, "after": a, "trend": None, "lift": None, "attributed": a["revenue"]}
    if not b["recipients"]:
        return {**out, "method": "attributed_only", "status": "pending",
                "reason": "It wasn't sending before the change, so there's nothing to compare. A holdout group will measure it."}
    if a["recipients"] < MIN_RECIPIENTS or b["recipients"] < MIN_RECIPIENTS:
        return {**out, "method": "before_after", "status": "too_small",
                "reason": f"Needs {MIN_RECIPIENTS:,} recipients before and since the change to read; "
                          f"has {b['recipients']:,.0f} before and {a['recipients']:,.0f} since."}
    trend = 1.0
    if ob["recipients"] >= MIN_RECIPIENTS and oa["recipients"] >= MIN_RECIPIENTS and ob["rate"] and oa["rate"] is not None:
        trend = min(max(oa["rate"] / ob["rate"], TREND_BOUNDS[0]), TREND_BOUNDS[1])
    lift = (a["rate"] - b["rate"] * trend) * a["recipients"]
    return {**out, "method": "before_after", "status": "measured", "trend": round(trend, 4), "lift": round(lift, 2),
            "reason": "Revenue per recipient before vs since the change, adjusted for the trend in flows it didn't touch."}


def parse_report(res: dict) -> dict:
    rows: dict = {}
    for r in ((res.get("data") or {}).get("attributes") or {}).get("results") or []:
        g, st = r.get("groupings") or {}, r.get("statistics") or {}
        key = (g.get("flow_id"), g.get("flow_message_id"))
        cur = rows.setdefault(key, {"recipients": 0.0, "revenue": 0.0})
        cur["recipients"] += float(st.get("recipients") or 0)
        cur["revenue"] += float(st.get("conversion_value") or 0)
    return rows


def read_window(client: Any, metric_id: str, start: datetime, end: datetime) -> dict:
    attrs = {"statistics": ["recipients", "conversion_value"], "conversion_metric_id": metric_id,
             "timeframe": {"start": start.astimezone(timezone.utc).isoformat(), "end": end.astimezone(timezone.utc).isoformat()},
             "group_by": ["flow_id", "flow_message_id"]}
    return parse_report(client.query("/api/flow-values-reports/", {"data": {"type": "flow-values-report", "attributes": attrs}}))


def due(session: Session, client_id: str, now: datetime) -> list[m.OpportunityOutcome]:
    return [o for o in session.scalars(select(m.OpportunityOutcome).where(
        m.OpportunityOutcome.client_id == client_id, m.OpportunityOutcome.shipped_at.is_not(None),
        m.OpportunityOutcome.status != "withdrawn")) if o.targets and now - _aware(o.shipped_at) >= MIN_LIVE]


def measure(session: Session, client: m.Client, outcomes: Iterable[m.OpportunityOutcome], read, now: datetime) -> list[m.ImpactReading]:
    """read(start, end) -> flow values. One pair of reads per distinct ship day; today's reading is replaced."""
    by_day: dict[str, list[m.OpportunityOutcome]] = {}
    for o in outcomes:
        by_day.setdefault(_aware(o.shipped_at).strftime("%Y-%m-%d"), []).append(o)
    out = []
    for day in sorted(by_day)[-MAX_SHIP_DATES:]:
        ship = _aware(by_day[day][0].shipped_at).replace(hour=0, minute=0, second=0, microsecond=0)
        before, after = read(ship - BEFORE, ship), read(ship, now)
        for o in by_day[day]:
            r = compute(o.targets, before, after)
            today = session.scalar(select(m.ImpactReading).where(m.ImpactReading.outcome_id == o.id)
                                   .order_by(m.ImpactReading.created_at.desc()).limit(1))
            if today is None or _aware(today.created_at).date() != now.date():
                today = m.ImpactReading(org_id=o.org_id, client_id=o.client_id, outcome_id=o.id)
                session.add(today)
            today.method, today.status, today.reason = r["method"], r["status"], r["reason"]
            today.before = {**r["before"], "start": (ship - BEFORE).isoformat(), "end": ship.isoformat()}
            today.after = {**r["after"], "start": ship.isoformat(), "end": now.isoformat()}
            today.trend, today.lift, today.attributed, today.created_at = r["trend"], r["lift"], r["attributed"], now
            out.append(today)
    session.flush()
    return out


# ---------------------------------------------------------------- progress

def latest_readings(session: Session, client_id: str) -> dict[str, m.ImpactReading]:
    out: dict[str, m.ImpactReading] = {}
    for r in session.scalars(select(m.ImpactReading).where(m.ImpactReading.client_id == client_id)
                             .order_by(m.ImpactReading.created_at)):
        out[r.outcome_id] = r
    return out


def progress(session: Session, client: m.Client, now: Optional[datetime] = None) -> dict:
    """The First 30 days card: measured lift toward the goal, attributed revenue beside it, one row per fix."""
    now = now or utcnow()
    start = _aware(client.created_at)
    day = max(1, (now - start).days + 1)
    readings = latest_readings(session, client.id)
    rows = []
    for o in session.scalars(select(m.OpportunityOutcome).where(m.OpportunityOutcome.client_id == client.id,
                                                                m.OpportunityOutcome.status != "withdrawn")
                             .order_by(m.OpportunityOutcome.approved_at)):
        r = readings.get(o.id)
        shipped = _aware(o.shipped_at)
        state = (r.status if r else "waiting" if shipped and now - shipped < MIN_LIVE else
                 "no_targets" if shipped and not o.targets else "live" if shipped else "approved")
        rows.append({"outcome_id": o.id, "opportunity_id": o.opportunity_id, "title": o.title, "state": state,
                     "approved_at": _aware(o.approved_at).isoformat(), "shipped_at": shipped.isoformat() if shipped else None,
                     "shipped_by": o.shipped_by, "targets": o.targets or [],
                     "readable_from": (shipped + MIN_LIVE).isoformat() if shipped else None,
                     "method": r.method if r else None, "lift": r.lift if r and r.status == "measured" else None,
                     "attributed": r.attributed if r else None, "before": r.before if r else None,
                     "after": r.after if r else None, "trend": r.trend if r else None,
                     "reason": r.reason if r else "", "read_at": _aware(r.created_at).isoformat() if r else None})
    measured = round(sum(x["lift"] for x in rows if x["lift"] is not None), 2)
    attributed = round(sum(x["attributed"] or 0 for x in rows), 2)
    return {"goal": GOAL, "window_days": WINDOW_DAYS, "started_at": start.isoformat(), "day": day,
            "days_left": max(0, WINDOW_DAYS - day + 1) if day <= WINDOW_DAYS else 0,
            "measured": measured, "attributed": attributed, "reached": measured >= GOAL,
            "approved": len(rows), "live": sum(1 for x in rows if x["shipped_at"]),
            "measured_fixes": sum(1 for x in rows if x["lift"] is not None), "fixes": rows}
