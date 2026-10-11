"""The client workspace as one typed bundle: every section, its state, its numbers and its evidence.

A bundle is built from one audit run (scorecard, analysis artifact and fact ledger), the brand's
onboarding answers and the latest dossier. Each section says what state it is in (queued, collecting,
ready, partial, unavailable) and, separately, whether its written parts are awaiting strategist review.
Every number a section shows is a fact id; bundle.facts carries each fact's basis (label, window, source,
time basis, evidence class) for the page's "Metric basis" expander.

publish() stores one version per scored run (workspace_bundles); current() returns the latest published
version, or a live bundle with honest collecting/queued states when nothing is published yet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models as m

SCHEMA_VERSION = 1
SECTION_STATES = ("queued", "collecting", "ready", "partial", "unavailable", "awaiting_review")
NARRATIVE_STATES = ("awaiting_review", "approved", "none")

# (id, title, analysis modules that feed it, has written parts a strategist reviews)
SECTIONS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("opportunities", "Opportunities", ("kpi_scorecard",), True),
    ("changes", "What changed", ("changes",), False),
    ("report", "Monthly report", ("kpi_scorecard", "flow_categories", "changes"), True),
    ("foundational", "Foundational report", (), True),
    ("performance", "Performance", ("performance", "kpi_scorecard", "flow_history"), True),
    ("cohorts", "Customer cohorts", ("cohorts",), True),
    ("journeys", "Customer journeys", ("journey_graph", "flow_categories", "flow_history"), True),
    ("calendar", "Send history", ("program", "promotions"), True),
    ("capture", "Capture & reach", ("capture_reach", "subscriptions"), True),
    ("deliverability", "Deliverability & messages", ("complaints", "messages"), True),
    ("plan", "90-day plan", (), True),
    ("answers", "Onboarding answers", (), False),
    ("brand", "Brand & guardrails", (), False),
    ("sources", "Sources & definitions", (), False),
)
DEFINITIONS = [
    ("Store revenue", "Gross value of Placed Order events from the order metric chosen for this account, all sources, "
                      "before refunds. The metric and why it was chosen are under Sources."),
    ("Attributed revenue", "Klaviyo's conversion value for flows and campaigns under the account's attribution window. "
                           "Attributed is not incremental: don't add it to store revenue or read it as lift."),
    ("Unavailable", "A number the account couldn't give us, shown with the reason. Unavailable is never shown as zero."),
    ("First-observed buyer", "A profile's first order inside the 24-month order window, after a 90-day burn-in. "
                             "Some may be returning customers whose earlier orders fall outside the window."),
    ("Mature horizon", "A cohort's repeat rate at N days is shown only once every buyer in it has had N days."),
    ("Interval", "Repeat rates carry a 95% Wilson interval; groups under 10 buyers are suppressed."),
    ("Time basis", "Event basis counts when the order or signup happened; send basis credits revenue to when "
                   "the message went out."),
    ("Evidence class", "Documented: read from the account. Inferred: derived with a stated assumption. "
                       "Proposed: a recommendation not yet approved."),
    ("Opportunity sizing", "Measured audience x labelled assumption rate x measured order value x months. Each audience "
                           "is counted once across levers. Modeled estimates, not forecasts or measured lift."),
    ("Proposed / UNCONFIRMED", "Every opportunity and plan item is proposed until a strategist and the brand approve it, "
                               "with an owner."),
]


class Section(BaseModel):
    id: str
    title: str
    state: str
    reason: str = ""
    narrative_state: str = "none"
    narrative: Optional[dict] = None
    modules: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    findings: list[dict] = Field(default_factory=list)
    tables: dict[str, list[dict]] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)


class Bundle(BaseModel):
    schema_version: int = SCHEMA_VERSION
    client: dict[str, Any]
    run: Optional[dict[str, Any]] = None
    version: Optional[int] = None
    status: str = "measured"
    generated_at: str
    facts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    sections: list[Section]


# ---------------------------------------------------------------- reading

def latest_run(session: Session, client_id: str) -> Optional[m.AuditRun]:
    return session.scalar(select(m.AuditRun).where(m.AuditRun.client_id == client_id)
                          .order_by(m.AuditRun.created_at.desc(), m.AuditRun.id.desc()).limit(1))


def _artifact(session: Session, run_id: str, name: str) -> Optional[dict]:
    a = session.scalar(select(m.AuditArtifact).where(m.AuditArtifact.run_id == run_id, m.AuditArtifact.name == name))
    return a.payload if a else None


def _run_view(run: m.AuditRun) -> dict[str, Any]:
    from .collector.run import LABELS
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {"id": run.id, "status": run.status, "track": run.track, "error": run.error or "",
            "started_at": iso(run.started_at), "finished_at": iso(run.finished_at),
            "stages": [{"key": k, "label": label, "status": (run.stages or {}).get(k, {}).get("status", "pending"),
                        "detail": (run.stages or {}).get(k, {}).get("detail", ""),
                        "error": (run.stages or {}).get(k, {}).get("error")} for k, label in LABELS.items()]}


def _module_state(run: Optional[m.AuditRun], scored: bool, analysis: Optional[dict],
                  modules: tuple[str, ...]) -> tuple[str, str]:
    if run is None:
        return "queued", "No Klaviyo audit has run for this brand yet."
    if run.status == "failed":
        return "unavailable", f"The audit run failed: {run.error or 'see Sources'}"
    if run.status in ("queued", "collecting", "scoring") or not scored or analysis is None:
        return "collecting", "Reading the account; this section fills in when the audit finishes."
    if not modules:
        return "ready", ""
    status = {k: (analysis.get("modules") or {}).get(k, {}) for k in modules}
    bad = {k: v.get("reason") or "not run" for k, v in status.items() if v.get("status") != "ok"}
    if not bad:
        return "ready", ""
    reason = "; ".join(f"{k.replace('_', ' ')}: {r}" for k, r in bad.items())
    return ("unavailable" if len(bad) == len(modules) else "partial"), reason


def _checks(session: Session, sc: m.Scorecard) -> list[dict]:
    from .audit.engine import results_of
    from .audit.library import specs as library_specs
    specs = library_specs()
    out = []
    for cid, r in sorted(results_of(session, sc).items()):
        spec = specs.get(cid) or {}
        out.append({"id": cid, "title": spec.get("title"), "area": spec.get("area"), "severity": spec.get("severity"),
                    "question": spec.get("question"), "why": spec.get("why_it_matters"), "verdict": r.verdict,
                    "message": r.message, "evidence": (r.evidence or [])[:4]})
    return out


def _metric_choices(totals: Optional[dict]) -> list[dict]:
    out = []
    for key, c in sorted(((totals or {}).get("metricChoices") or {}).items()):
        if not isinstance(c, dict):
            continue
        out.append({"key": key, "used": c.get("used"), "status": c.get("status"), "reason": c.get("reason"),
                    "candidates": c.get("candidates") or []})
    return out


OUTCOME_LABEL = {"tracking": "Approved", "improved": "Read out: improved", "flat": "Read out: flat",
                 "worse": "Read out: worse", "unmeasured": "Read out: unmeasured", "withdrawn": "Withdrawn"}


def _with_outcomes(cards: list[dict], tracked: list) -> list[dict]:
    """Cards with the latest approval on each (status, owner, readout) instead of Proposed / UNCONFIRMED."""
    latest: dict[str, Any] = {}
    for o in tracked:  # newest first
        latest.setdefault(o.opportunity_id, o)
    out = []
    for c in cards:
        o = latest.get(c["id"])
        if o is None:
            out.append(c)
            continue
        out.append({**c, "status": OUTCOME_LABEL.get(o.status, o.status), "owner": o.owner, "outcome_id": o.id,
                    "outcome_status": o.status, "due_at": o.due_at.isoformat() if o.due_at else None,
                    "verdict_reason": o.verdict_reason})
    return out


def build(session: Session, client: m.Client, run: Optional[m.AuditRun] = None) -> Bundle:
    """The bundle for one run (default: the client's latest), with live states."""
    from . import intake as intake_mod
    run = run if run is not None else latest_run(session, client.id)
    sc = session.get(m.Scorecard, run.scorecard_id) if run is not None and run.scorecard_id else None
    analysis = _artifact(session, run.id, "analysis") if run is not None else None
    summary = (sc.summary or {}) if sc else {}
    facts: dict[str, dict] = {}
    if run is not None:
        for f in session.scalars(select(m.AuditFact).where(m.AuditFact.run_id == run.id)):
            facts[f.fact_id] = {"value": (f.value or {}).get("v"), "unit": f.unit, "label": f.label, "window": f.window,
                                "time_basis": f.time_basis, "source": f.source, "module": f.module,
                                "evidence_class": f.evidence_class}
    findings = (analysis or {}).get("findings") or []
    tables = {k: v.get("tables") or {} for k, v in ((analysis or {}).get("modules") or {}).items()}
    row = session.scalar(select(m.OnboardingIntake).where(m.OnboardingIntake.client_id == client.id))
    answers = (row.answers if row else {}) or {}
    dossier = session.scalar(select(m.Dossier).where(m.Dossier.client_id == client.id).order_by(m.Dossier.version.desc()).limit(1))
    doc = ((dossier.document or {}).get("dossier") or dossier.document or {}) if dossier else {}

    from .loop import outcomes as outcomes_mod
    from .narrative.review import narrative_for, published
    drafts = narrative_for(session, run.id) if run is not None else {}
    tracked = outcomes_mod.for_client(session, client.id)
    sections = []
    for sid, title, modules, written in SECTIONS:
        state, reason = _module_state(run, sc is not None, analysis, modules)
        s = Section(id=sid, title=title, state=state, reason=reason, modules=list(modules),
                    narrative_state="awaiting_review" if written else "none")
        if written and (story := published(drafts.get(sid))) is not None:
            s.narrative, s.narrative_state = story, "approved"
        s.facts = [fid for fid, f in facts.items() if f["module"] in modules]
        s.findings = [f for f in findings if f["module"] in modules]
        s.tables = {f"{mod}.{name}": rows for mod in modules for name, rows in (tables.get(mod) or {}).items()}
        if sid == "opportunities":
            s.data = {k: summary.get(k) for k in ("opportunities", "total", "sizing", "gaps", "playbook_version")}
            s.data["opportunities"] = _with_outcomes(summary.get("opportunities") or [], tracked)
            s.data["outcomes"] = [outcomes_mod.view(o) for o in tracked]
            s.findings = findings
        elif sid == "changes":
            s.data = {"outcomes": [outcomes_mod.view(o) for o in tracked if o.status != "tracking"]}
        elif sid == "report":
            s.data = {"month": run.created_at.strftime("%B %Y") if run is not None else None,
                      "refresh": ((run.context or {}).get("refresh") or "first audit") if run is not None else None,
                      "score": summary.get("score"),
                      "top": [{k: c.get(k) for k in ("id", "rank", "title", "category", "severity", "status", "owner")}
                              for c in _with_outcomes(summary.get("opportunities") or [], tracked)[:5]],
                      "outcomes": [outcomes_mod.view(o) for o in tracked]}
        elif sid == "foundational":
            if sc is not None:
                s.data = {"score": summary.get("score"), "by_area": summary.get("by_area"), "by_verdict": summary.get("by_verdict"),
                          "coverage": summary.get("coverage"), "score_formula": summary.get("score_formula"),
                          "checks": _checks(session, sc), "as_of": sc.as_of.isoformat() if sc.as_of else None}
        elif sid == "plan":
            plan = doc.get("first_30_day_plan") or {}
            s.data = {"plan": plan, "campaign": (dossier.document or {}).get("creative_draft") if dossier else None,
                      "flows": [{"flow_key": f.get("flow_key"), "name": f.get("name"), "already_live": f.get("already_live"),
                                 "messages": [{"subject": d.get("subject_line"), "preview": d.get("preview_text")}
                                              for d in f.get("message_drafts") or []]}
                                for f in (doc.get("execution") or {}).get("flows") or []]}
            s.state, s.reason = (("ready", "") if plan or s.data["campaign"] or s.narrative
                                 else ("queued", "No plan has been drafted yet."))
        elif sid == "answers":
            s.data = {"sections": [{"title": sec["title"], "questions": [{"key": q["key"], "label": q["label"]}
                                                                         for q in sec["questions"]]} for sec in intake_mod.SECTIONS],
                      "answers": answers, "submitted_at": row.submitted_at.isoformat() if row and row.submitted_at else None}
            s.state, s.reason = ("ready", "") if answers else ("queued", "The brand hasn't answered the onboarding questions yet.")
        elif sid == "brand":
            s.data = {"brand_identity": doc.get("brand_identity") or {}, "economics": doc.get("economics") or {},
                      "campaign_directives": doc.get("campaign_directives") or {},
                      "stated": {k: answers.get(k) for k in ("avoid_claims", "regulated_category", "kpis", "never_sound_like",
                                                             "voice_words", "discount_stance", "deepest_discount", "sms_platform")}}
            s.state, s.reason = ("ready", "") if doc or answers else ("queued", "Nothing about the brand is on file yet.")
        elif sid == "sources":
            s.data = {"definitions": [{"term": t, "meaning": d} for t, d in DEFINITIONS],
                      "metric_choices": _metric_choices(_artifact(session, run.id, "totals")) if run is not None else [],
                      "analysis": summary.get("analysis") or {}, "missing_files": summary.get("missing_files") or [],
                      "gaps": summary.get("gaps") or []}
            s.state, s.reason = "ready", ""
        sections.append(s)
    written = [s for s in sections if s.narrative_state != "none"]
    return Bundle(client={"slug": client.slug, "name": client.name, "timezone": client.timezone},
                  run=_run_view(run) if run is not None else None, generated_at=datetime.now(timezone.utc).isoformat(),
                  status="reviewed" if written and all(s.narrative_state == "approved" for s in written) else "measured",
                  facts=facts, sections=sections)


# ---------------------------------------------------------------- storing

def publish(session: Session, run: m.AuditRun) -> m.WorkspaceBundle:
    """Store (or refresh) the bundle for one scored run as the client's next workspace version."""
    client = session.get(m.Client, run.client_id)
    bundle = build(session, client, run)
    row = session.scalar(select(m.WorkspaceBundle).where(m.WorkspaceBundle.run_id == run.id))
    if row is None:
        last = session.scalar(select(func.max(m.WorkspaceBundle.version)).where(m.WorkspaceBundle.client_id == client.id))
        row = m.WorkspaceBundle(org_id=run.org_id, client_id=client.id, run_id=run.id, version=(last or 0) + 1)
        session.add(row)
    bundle.version = row.version
    row.bundle = bundle.model_dump(mode="json")
    row.status = bundle.status
    row.updated_at = datetime.now(timezone.utc)
    session.flush()
    return row


def versions(session: Session, client_id: str) -> list[dict]:
    rows = session.execute(select(m.WorkspaceBundle.version, m.WorkspaceBundle.created_at, m.WorkspaceBundle.status)
                           .where(m.WorkspaceBundle.client_id == client_id).order_by(m.WorkspaceBundle.version.desc())).all()
    return [{"version": v, "created_at": at.isoformat(), "status": st} for v, at, st in rows]


def current(session: Session, client: m.Client, version: Optional[int] = None) -> Optional[dict]:
    """A published version (default: the latest), with the in-progress run if a newer one is collecting.
    None when the version asked for doesn't exist."""
    q = select(m.WorkspaceBundle).where(m.WorkspaceBundle.client_id == client.id)
    row = session.scalar(q.where(m.WorkspaceBundle.version == version) if version
                         else q.order_by(m.WorkspaceBundle.version.desc()).limit(1))
    if version and row is None:
        return None
    run = latest_run(session, client.id)
    if row is None:
        out = build(session, client, run).model_dump(mode="json")
    else:
        out = dict(row.bundle)
        live = build(session, client, session.get(m.AuditRun, row.run_id))
        # onboarding answers and brand rules change after publishing; show the latest
        keep = {s.id: s.model_dump(mode="json") for s in live.sections if s.id in ("answers", "brand", "plan")}
        out["sections"] = [keep.get(s["id"], s) for s in out["sections"]]
    out["versions"] = versions(session, client.id)
    out["in_progress"] = _run_view(run) if run is not None and (row is None or run.id != row.run_id) \
        and run.status in ("queued", "collecting", "scoring", "ready", "partial") and not run.scorecard_id else None
    return out
