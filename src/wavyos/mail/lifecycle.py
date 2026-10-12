"""The 30-day onboarding sequence: from sign-up, through the audit, to the first $10K of measured lift.

Each pass (the worker's scheduler tick) works out every self-serve brand's state from the tables that already
record it (Brand), and queues each email whose moment has come. An email is due at a time computed from that
state; it's queued once per person, only while it's fresh (a brand that signed up before this shipped doesn't
get a backlog), and nudges are paced (outbox.NUDGE_GAP) and wait out quiet hours.

next_step() is the one thing the brand (or Wavy) should do now. The emails end with it and the workspace's
First 30 days card shows it, so they never disagree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import impact
from .. import models as m
from ..db import utcnow
from . import outbox, templates
from .render import Email, money_range

DAY = timedelta(days=1)
FRESH = timedelta(days=2)          # an email whose moment passed longer ago than this is never queued
HORIZON = timedelta(days=45)       # brands older than this are past onboarding
KEY_ERROR = re.compile(r"\b40[13]\b|unauthori[sz]ed|forbidden|permission|scope|invalid api key|revoked|api key", re.I)


@dataclass
class Links:
    connect: str
    questions: str
    workspace: str
    opportunities: str
    console: str


@dataclass
class Brand:
    client: m.Client
    people: list[m.User]
    now: datetime
    signed_up: datetime
    links: Links
    connected: Optional[datetime] = None
    first_run: Optional[m.AuditRun] = None
    problem: Optional[dict] = None
    ready: Optional[datetime] = None
    reviewed: Optional[datetime] = None
    summary: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    intake_submitted: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    progress: dict = field(default_factory=dict)
    next_step: Optional[dict] = None

    @property
    def day(self) -> int:
        return max(1, (self.now - self.signed_up).days + 1)


aware = outbox.aware


def links_for(client: m.Client, app_url: str) -> Links:
    slug = quote(client.slug)
    return Links(connect=f"{app_url}/onboard?client={slug}", questions=f"{app_url}/onboard?client={slug}",
                 workspace=f"{app_url}/audit/{slug}", opportunities=f"{app_url}/audit/{slug}#opportunities",
                 console=f"{app_url}/console")


def brand_people(session: Session, client_id: str) -> list[m.User]:
    return list(session.scalars(select(m.User).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.Membership.client_id == client_id, m.Membership.role.in_(("client_owner", "client_approver")),
        m.User.active.is_(True)).order_by(m.User.created_at)))


def staff_people(session: Session, org_id: str) -> list[m.User]:
    return list(session.scalars(select(m.User).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.Membership.org_id == org_id, m.Membership.client_id.is_(None),
        m.Membership.role.in_(("org_admin", "account_lead")), m.User.active.is_(True), m.User.kind == "staff")
        .distinct().order_by(m.User.created_at)))


def self_serve(session: Session, client: m.Client) -> bool:
    if (client.settings or {}).get("self_serve"):
        return True
    return session.scalar(select(m.Membership.id).where(m.Membership.client_id == client.id,
                                                        m.Membership.role == "client_owner").limit(1)) is not None


def _problem(session: Session, run: Optional[m.AuditRun]) -> Optional[dict]:
    if run is None or run.status != "failed":
        return None
    from ..collector.run import LABELS
    failed = next(((k, v) for k, v in (run.stages or {}).items() if v.get("status") == "failed"), (None, {}))
    error = (failed[1].get("error") or run.error or "")[:500]
    return {"run_id": run.id, "at": aware(run.finished_at or run.created_at), "stage": LABELS.get(failed[0], "connecting"),
            "error": error, "key": failed[0] == "verify" or bool(KEY_ERROR.search(error))}


def load(session: Session, client: m.Client, app_url: str, now: Optional[datetime] = None) -> Brand:
    now = now or utcnow()
    people = brand_people(session, client.id)
    b = Brand(client=client, people=people, now=now, signed_up=aware(client.created_at), links=links_for(client, app_url))
    b.connected = aware(session.scalar(select(func.min(m.Connection.created_at)).where(
        m.Connection.client_id == client.id, m.Connection.provider == "klaviyo", m.Connection.status == "active")))
    runs = session.scalars(select(m.AuditRun).where(m.AuditRun.client_id == client.id).order_by(m.AuditRun.created_at)).all()
    b.first_run = runs[0] if runs else None
    if b.first_run is not None:  # an audit that ran means Klaviyo was connected, even if the connection was later removed
        b.connected = min(filter(None, (b.connected, aware(b.first_run.created_at))))
    b.problem = _problem(session, runs[-1] if runs else None)
    b.ready = aware(session.scalar(select(func.min(m.WorkspaceBundle.created_at)).where(m.WorkspaceBundle.client_id == client.id)))
    b.reviewed = aware(session.scalar(select(func.min(m.WorkspaceBundle.updated_at)).where(
        m.WorkspaceBundle.client_id == client.id, m.WorkspaceBundle.status == "reviewed")))
    scored = next((r for r in reversed(runs) if r.scorecard_id), None)
    if scored is not None:
        b.summary = (session.get(m.Scorecard, scored.scorecard_id).summary or {})
        b.facts = {f.fact_id: (f.value or {}).get("v") for f in session.scalars(
            select(m.AuditFact).where(m.AuditFact.run_id == scored.id, m.AuditFact.module.in_(("journey_map", "kpi_scorecard"))))}
    intake = session.scalar(select(m.OnboardingIntake).where(m.OnboardingIntake.client_id == client.id))
    b.intake_submitted = aware(intake.submitted_at) if intake else None
    if people:
        b.last_seen = aware(session.scalar(select(func.max(m.ApiToken.last_used_at)).where(
            m.ApiToken.user_id.in_([p.id for p in people]))))
    b.progress = impact.progress(session, client, now)
    b.next_step = next_step(b)
    return b


# ---------------------------------------------------------------- the one next step

def _approved_ids(b: Brand) -> set:
    return {x["opportunity_id"] for x in b.progress.get("fixes") or []}


def next_step(b: Brand) -> dict:
    L, p = b.links, b.progress

    def step(key, owner, title, detail, cta, url):
        return {"key": key, "owner": owner, "title": title, "detail": detail, "cta": cta, "url": url}

    if not b.connected:
        return step("connect", "you", "Connect Klaviyo", "A read-only key starts your audit.",
                    "Connect Klaviyo", L.connect)
    if b.problem and b.problem["key"]:
        return step("new_key", "you", "Paste a new Klaviyo key", "Klaviyo refused the last key, so the audit stopped.",
                    "Paste a new key", L.connect)
    if not b.ready:
        return step("audit_running", "wavy", "Your audit is running", "We'll email you the moment it's ready.",
                    "Watch it run", L.connect)
    cards = sorted(b.summary.get("opportunities") or [], key=lambda c: c.get("rank") or 999)
    ranked = [c for c in cards if (c.get("sizing") or {}).get("status") == "sized"]
    ranked += [c for c in cards if c not in ranked]   # sized levers first, then the rest in rank order

    def worth(c: dict) -> str:
        z = c.get("sizing") or {}
        return f", worth {money_range(z.get('annual'))}" if z.get("status") == "sized" else ""

    if not p.get("approved"):
        top = ranked[0] if ranked else None
        return step("approve", "you", "Approve your first fixes",
                    (f"Start with \"{top['title']}\"{worth(top)}." if top else
                     "Pick the fixes worth making from your ranked list."), "Approve fixes",
                    f"{L.opportunities}/{quote(top['id'])}" if top else L.opportunities)
    if not p.get("live"):
        return step("shipping", "wavy", "Wavy is building your approved fixes",
                    f"{p['approved']} approved, none live yet. We'll email you when the first one goes live.",
                    "See your fixes", L.workspace)
    if not p.get("measured_fixes"):
        first = min((x["readable_from"] for x in p["fixes"] if x["readable_from"]), default=None)
        return step("measuring", "wavy", "Measuring your live fixes",
                    "The first reading lands after 7 days of sends" + (f", from {first[:10]}." if first else "."),
                    "See progress", L.workspace)
    nxt = next((c for c in ranked if c["id"] not in _approved_ids(b)), None)
    if not p.get("reached"):
        if nxt:
            return step("next_fix", "you", f"Approve the next fix: {nxt['title']}",
                        f"Next on your list{worth(nxt)}. Every fix live sooner adds to your measured total.",
                        "Approve it", f"{L.opportunities}/{quote(nxt['id'])}")
        return step("measuring", "wavy", "Measuring your live fixes", "Every ranked fix is approved. Readings update daily.",
                    "See progress", L.workspace)
    return step("keep_going", "you", "Goal reached. Keep going",
                (f"Next on the list: \"{nxt['title']}\"." if nxt else "Your next audit refresh will rank what's next."),
                "See what's next", L.opportunities)


# ---------------------------------------------------------------- the sequence

@dataclass(frozen=True)
class Step:
    key: str
    kind: str                                   # transactional | nudge | staff
    when: Callable[[Brand], Optional[datetime]]  # when it's due; None: not now (or never)
    render: Callable[[Brand, m.User], Email]
    live: bool = True                           # False: written, switched off until its feature exists
    suffix: Optional[Callable[[Brand], str]] = None   # repeats and per-event emails get their own key

    @property
    def audience(self) -> str:
        return "staff" if self.kind == "staff" else "brand"


def _connect_after(days: int):
    return lambda b: None if b.connected else b.signed_up + days * DAY


def _first_shipped(b: Brand) -> Optional[datetime]:
    shipped = [x["shipped_at"] for x in b.progress.get("fixes") or [] if x["shipped_at"]]
    return datetime.fromisoformat(min(shipped)) if shipped else None


def _first_lift(b: Brand) -> Optional[datetime]:
    read = [x["read_at"] for x in b.progress.get("fixes") or [] if x["lift"] is not None and x["read_at"]]
    return datetime.fromisoformat(min(read)) if read else None


def _week(b: Brand) -> int:
    first = _first_shipped(b)
    return int((b.now - first) / timedelta(days=7)) if first else 0


def _weekly(b: Brand) -> Optional[datetime]:
    first, n = _first_shipped(b), _week(b)
    if first is None or n < 1 or b.day > HORIZON.days:
        return None
    return first + n * timedelta(days=7)


def _stalled(b: Brand) -> Optional[datetime]:
    return None if not b.ready or b.progress.get("live") else b.signed_up + 14 * DAY


def _newest_unshipped(b: Brand) -> Optional[datetime]:
    waiting = [x["approved_at"] for x in b.progress.get("fixes") or [] if not x["shipped_at"]]
    return datetime.fromisoformat(max(waiting)) if waiting else None


SEQUENCE: tuple[Step, ...] = (
    Step("welcome", "transactional", lambda b: b.signed_up, templates.welcome),
    Step("connect_nudge", "nudge", _connect_after(1), templates.connect_nudge),
    Step("connect_help", "nudge", _connect_after(3), templates.connect_help),
    Step("connect_last", "nudge", _connect_after(7), templates.connect_last),
    Step("audit_started", "transactional",
         lambda b: None if b.ready or b.first_run is None else aware(b.first_run.created_at), templates.audit_started),
    Step("audit_problem", "transactional", lambda b: b.problem["at"] if b.problem else None, templates.audit_problem,
         suffix=lambda b: b.problem["run_id"]),
    Step("audit_ready", "transactional", lambda b: b.ready, templates.audit_ready),
    Step("questions_nudge", "nudge", lambda b: None if not b.ready or b.intake_submitted else b.ready + DAY,
         templates.questions_nudge),
    Step("review_nudge", "nudge",
         lambda b: None if not b.ready or (b.last_seen and b.last_seen >= b.ready) else b.ready + 2 * DAY, templates.review_nudge),
    Step("story_ready", "nudge", lambda b: b.reviewed, templates.story_ready),
    Step("approve_fixes", "nudge", lambda b: None if not b.ready or b.progress.get("approved") else b.ready + 3 * DAY,
         templates.approve_fixes),
    Step("write_access", "nudge", lambda b: None, templates.write_access, live=False),  # needs the Klaviyo write connector
    Step("fix_live", "transactional", _first_shipped, templates.fix_live),
    Step("first_lift", "transactional", _first_lift, templates.first_lift),
    Step("weekly_impact", "nudge", _weekly, templates.weekly_impact, suffix=lambda b: str(_week(b))),
    Step("milestone_10k", "transactional", lambda b: b.now if b.progress.get("reached") else None, templates.milestone_10k),
    Step("day30_summary", "nudge", lambda b: b.signed_up + 30 * DAY if b.connected else None, templates.day30_summary),
    Step("stalled", "nudge", _stalled, templates.stalled),
    Step("staff_signup", "staff", lambda b: b.signed_up, templates.staff_signup),
    Step("staff_review_needed", "staff", lambda b: None if b.reviewed or not b.ready else b.ready, templates.staff_review_needed),
    Step("staff_fixes_approved", "staff", _newest_unshipped, templates.staff_fixes_approved,
         suffix=lambda b: str(b.progress.get("approved", 0))),
    Step("staff_stalled", "staff", _stalled, templates.staff_stalled),
    Step("staff_audit_failed", "staff", lambda b: b.problem["at"] if b.problem and not b.problem["key"] else None,
         templates.staff_audit_failed, suffix=lambda b: b.problem["run_id"]),
)
STEPS = {s.key: s for s in SEQUENCE}


def email_key(step: Step, b: Brand) -> str:
    key = step.key + (f":{b.client.id}" if step.audience == "staff" else "")
    return key + (f":{step.suffix(b)}" if step.suffix else "")


def brands(session: Session, now: datetime, org_id: Optional[str] = None) -> list[m.Client]:
    q = select(m.Client).where(m.Client.status.in_(("onboarding", "active")), m.Client.created_at >= now - HORIZON)
    if org_id:
        q = q.where(m.Client.org_id == org_id)
    return [c for c in session.scalars(q.order_by(m.Client.created_at)) if self_serve(session, c)]


def plan(session: Session, settings, vault, now: Optional[datetime] = None, org_id: Optional[str] = None) -> dict:
    """One pass: queue every email that's due. Returns counts for the worker log."""
    now = now or utcnow()
    counts: dict[str, int] = {}
    staff_by_org: dict[str, list[m.User]] = {}
    for client in brands(session, now, org_id):
        b = load(session, client, settings.app_url, now)
        nudged: set[str] = set()
        for step in SEQUENCE:
            if not step.live:
                continue
            due = step.when(b)
            if due is None or due > now or now - due > FRESH:
                continue
            if step.audience == "staff":
                people = staff_by_org.setdefault(client.org_id, staff_people(session, client.org_id))
            else:
                if outbox.paused(client):
                    continue
                people = b.people
            key = email_key(step, b)
            for u in people:
                if step.kind == "nudge":
                    last = outbox.last_nudge(session, u.id)
                    if u.id in nudged or (last is not None and now - last < outbox.NUDGE_GAP):
                        continue
                row = outbox.queue(session, user=u, client=client, key=key, kind=step.kind, email=step.render(b, u),
                                   settings=settings, vault=vault, now=now, data={"step": step.key, "day": b.day})
                if row is not None:
                    counts[row.status] = counts.get(row.status, 0) + 1
                    if step.kind == "nudge" and row.status == "queued":
                        nudged.add(u.id)
    return counts


def preview(session: Session, client: m.Client, key: str, app_url: str, now: Optional[datetime] = None,
            user: Optional[m.User] = None) -> Email:
    """Render one step for one brand as it would go out now, whether or not it's due (for review)."""
    if key not in STEPS:
        raise KeyError(f"no onboarding email {key!r}; one of: {', '.join(STEPS)}")
    b = load(session, client, app_url, now)
    u = user or (b.people[0] if b.people else m.User(email="you@example.com", name=""))
    return STEPS[key].render(b, u)
