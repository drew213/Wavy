"""All tables. Every client-owned row carries org_id and client_id.

Status values are plain strings checked by the services (see the *_STATUSES tuples),
so the same schema works on SQLite and Postgres without native enums.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, Float, ForeignKey, Index, Integer, LargeBinary, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, IdMixin

# ---------------------------------------------------------------- tenancy

class Organization(IdMixin, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)


CLIENT_STATUSES = ("onboarding", "active", "paused", "offboarded")


class Client(IdMixin, Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("org_id", "slug"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="onboarding")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    website: Mapped[Optional[str]] = mapped_column(String(300))
    # {"allowed_providers": ["anthropic", "codex"], "daily_budget_usd": 10}. Client data is sent only to
    # providers listed here; the default is none, so each client opts in explicitly.
    settings: Mapped[dict] = mapped_column(default=dict)


USER_KINDS = ("staff", "client")


class User(IdMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("org_id", "email"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10), default="staff")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# Staff roles act across the agency; client roles belong to one client's stakeholders.
STAFF_ROLES = ("org_admin", "account_lead", "strategist", "analyst", "creative", "qa")
CLIENT_ROLES = ("client_owner", "client_approver", "client_viewer")


class Membership(IdMixin, Base):
    """A user's role. client_id NULL means the role applies to every client in the org."""
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "client_id", "role"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"), index=True)
    role: Mapped[str] = mapped_column(String(30))


class ApiToken(IdMixin, Base):
    __tablename__ = "api_tokens"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    last_used_at: Mapped[Optional[datetime]]
    revoked_at: Mapped[Optional[datetime]]


class OnboardingIntake(IdMixin, Base):
    """A brand's lifecycle onboarding answers, saved as they type. One per client."""
    __tablename__ = "onboarding_intakes"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), unique=True)
    answers: Mapped[dict] = mapped_column(default=dict)
    updated_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[Optional[datetime]] = mapped_column()
    submitted_at: Mapped[Optional[datetime]] = mapped_column()


class PasswordCredential(IdMixin, Base):
    """A browser sign-in password for a user. Only a salted scrypt hash is stored."""
    __tablename__ = "password_credentials"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))


AGENT_ROLES = ("account_lead", "analyst", "strategist", "creative", "qa")


class Agent(IdMixin, Base):
    """An AI worker. Every action it takes is attributed to its role."""
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("org_id", "role", "name"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    role: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------- knowledge (layers 1 and 2)

STANDARD_STATUSES = ("active", "superseded")


class Standard(IdMixin, Base):
    """Layer 1: an agency SOP, framework, template or benchmark. Versioned, never edited in place."""
    __tablename__ = "standards"
    __table_args__ = (UniqueConstraint("org_id", "key", "version"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    area: Mapped[str] = mapped_column(String(40))
    component: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")
    source: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[str] = mapped_column(String(80))


FACT_STATUSES = ("proposed", "approved", "rejected", "retired")
FACT_SOURCES = ("intake", "meeting", "data", "document", "agent", "person")


class ClientFact(IdMixin, Base):
    """Layer 2: something true about one client. Proposed first; approved only through an Approval."""
    __tablename__ = "client_facts"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    area: Mapped[str] = mapped_column(String(40))
    component: Mapped[str] = mapped_column(String(30))
    key: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(300))
    value: Mapped[dict] = mapped_column(default=dict)
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    source_kind: Mapped[str] = mapped_column(String(20))
    source_ref: Mapped[str] = mapped_column(String(500), default="")
    proposed_by: Mapped[str] = mapped_column(String(80))
    approval_id: Mapped[Optional[str]] = mapped_column(ForeignKey("approvals.id"))
    verified_by: Mapped[Optional[str]] = mapped_column(String(80))
    verified_at: Mapped[Optional[datetime]]
    review_by: Mapped[Optional[date]] = mapped_column(Date)
    supersedes_id: Mapped[Optional[str]] = mapped_column(ForeignKey("client_facts.id"))


# ---------------------------------------------------------------- operating chain (layer 3)

SEVERITIES = ("critical", "high", "medium", "low", "info")
INSIGHT_STATUSES = ("open", "actioned", "dismissed")


class Insight(IdMixin, Base):
    __tablename__ = "insights"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    area: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    evidence: Mapped[list] = mapped_column(default=list)  # [{kind, ref, detail}]
    source_kind: Mapped[str] = mapped_column(String(20))   # check | monitor | agent | person
    source_ref: Mapped[str] = mapped_column(String(300), default="")
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_by: Mapped[str] = mapped_column(String(80))


DECISION_STATUSES = ("proposed", "approved", "rejected", "closed")


class Decision(IdMixin, Base):
    """What we agreed to do about one or more insights. scope lists the approved_plan actions it covers."""
    __tablename__ = "decisions"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    scope: Mapped[list] = mapped_column(default=list)          # action keys this plan authorizes
    measurement: Mapped[dict] = mapped_column(default=dict)    # {metric, window_days, baseline}
    proposed_by: Mapped[str] = mapped_column(String(80))
    approval_id: Mapped[Optional[str]] = mapped_column(ForeignKey("approvals.id"))
    outcome: Mapped[str] = mapped_column(Text, default="")
    closed_at: Mapped[Optional[datetime]]
    insights: Mapped[list["Insight"]] = relationship(secondary="decision_insights")


class DecisionInsight(Base):
    __tablename__ = "decision_insights"
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), primary_key=True)
    insight_id: Mapped[str] = mapped_column(ForeignKey("insights.id"), primary_key=True)


TASK_STATUSES = ("todo", "in_progress", "blocked", "review", "done", "cancelled")


class Task(IdMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (CheckConstraint("decision_id IS NOT NULL OR job_run_id IS NOT NULL", name="has_origin"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    decision_id: Mapped[Optional[str]] = mapped_column(ForeignKey("decisions.id"), index=True)
    job_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("job_runs.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    area: Mapped[str] = mapped_column(String(40))
    owner_role: Mapped[str] = mapped_column(String(30))
    assignee_id: Mapped[Optional[str]] = mapped_column(String(80))
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    done_when: Mapped[str] = mapped_column(Text, default="")
    # Fixed when the task is created (e.g. brief, copy, draft_build, preflight, release_verification,
    # post_send_review, plan, readout). The executor produces only this phase; QA judges only this phase.
    phase: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="todo")
    checklist: Mapped[list] = mapped_column(default=list)   # [{id, text, passed, by}]
    outputs: Mapped[list] = mapped_column(default=list)     # [{kind, ref, label}]
    created_by: Mapped[str] = mapped_column(String(80))
    completed_at: Mapped[Optional[datetime]]


RESULT_STATUSES = ("pending", "measured", "inconclusive")


class Result(IdMixin, Base):
    __tablename__ = "results"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True)
    metric: Mapped[str] = mapped_column(String(120))
    baseline: Mapped[Optional[float]] = mapped_column(Float)
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    observed: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    notes: Mapped[str] = mapped_column(Text, default="")
    measured_by: Mapped[Optional[str]] = mapped_column(String(80))
    measured_at: Mapped[Optional[datetime]]


class MetricSnapshot(IdMixin, Base):
    __tablename__ = "metric_snapshots"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    source: Mapped[str] = mapped_column(String(40))
    metric_key: Mapped[str] = mapped_column(String(120), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    value: Mapped[Optional[float]] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(default=dict)


# ---------------------------------------------------------------- governance

APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired", "cancelled")


class Approval(IdMixin, Base):
    __tablename__ = "approvals"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    subject_kind: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decided_by: Mapped[Optional[str]] = mapped_column(String(80))
    decided_at: Mapped[Optional[datetime]]
    expires_at: Mapped[Optional[datetime]]
    used_at: Mapped[Optional[datetime]]
    note: Mapped[str] = mapped_column(Text, default="")


class PolicyOverride(IdMixin, Base):
    """Narrows (or, within org limits, relaxes) an action's permission level for one client or the org."""
    __tablename__ = "policy_overrides"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    level: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[Optional[datetime]]


# ---------------------------------------------------------------- jobs

JOB_RUN_STATUSES = ("queued", "running", "succeeded", "no_action", "failed", "skipped")


class JobRun(IdMixin, Base):
    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_job_runs_status_due", "status", "due_at"),)  # the claim and stuck-run queries
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    job_key: Mapped[str] = mapped_column(String(80), index=True)
    run_key: Mapped[str] = mapped_column(String(200), unique=True)
    due_at: Mapped[datetime]
    trigger: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[Optional[datetime]]
    heartbeat_at: Mapped[Optional[datetime]]  # touched while running; a stale one means the worker died
    finished_at: Mapped[Optional[datetime]]
    summary: Mapped[str] = mapped_column(Text, default="")
    produced: Mapped[dict] = mapped_column(default=dict)   # {insights: [], tasks: [], approvals: []}
    error: Mapped[str] = mapped_column(Text, default="")
    cost_usd: Mapped[Optional[float]] = mapped_column(Float)


class RateLimit(IdMixin, Base):
    """Attempts per key per window, shared by every web process. Keys are hashed: no IPs or emails stored."""
    __tablename__ = "rate_limits"
    __table_args__ = (UniqueConstraint("key", "bucket"),)
    key: Mapped[str] = mapped_column(String(64))
    bucket: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(index=True)


# ---------------------------------------------------------------- durable onboard output and commerce ingest


class Dossier(IdMixin, Base):
    """One immutable version of a client's master dossier, including flow payloads and QA."""
    __tablename__ = "dossiers"
    __table_args__ = (UniqueConstraint("client_id", "version"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="ACTIVE_EXECUTION")
    document: Mapped[dict] = mapped_column(default=dict)
    job_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("job_runs.id"), index=True)


class JobProgress(IdMixin, Base):
    """Live stage-by-stage state of a long job, committed as it goes so a page can watch it."""
    __tablename__ = "job_progress"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    job_run_id: Mapped[str] = mapped_column(ForeignKey("job_runs.id"), unique=True)
    state: Mapped[dict] = mapped_column(default=dict)


class IngestCursor(IdMixin, Base):
    """Where a paginated provider pull stopped, so the next run resumes instead of starting over."""
    __tablename__ = "ingest_cursors"
    __table_args__ = (UniqueConstraint("client_id", "source"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    source: Mapped[str] = mapped_column(String(40))
    next_url: Mapped[Optional[str]] = mapped_column(Text)
    watermark: Mapped[Optional[datetime]]
    pages_pulled: Mapped[int] = mapped_column(Integer, default=0)
    orders_stored: Mapped[int] = mapped_column(Integer, default=0)


class CommerceOrder(IdMixin, Base):
    """One order from a store. Upserted by external id so pages can be retried."""
    __tablename__ = "commerce_orders"
    __table_args__ = (UniqueConstraint("client_id", "source", "external_id"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    source: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(64))
    customer_key: Mapped[str] = mapped_column(String(120), index=True)
    ordered_at: Mapped[datetime] = mapped_column(index=True)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    gateway_sku: Mapped[str] = mapped_column(String(300), default="")
    refunded: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------- connectors

CONNECTION_STATUSES = ("active", "revoked", "error")


class Connection(IdMixin, Base):
    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("client_id", "provider", "external_account_id"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    external_account_id: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_ref: Mapped[str] = mapped_column(String(100))
    scopes: Mapped[list] = mapped_column(default=list)
    created_by: Mapped[str] = mapped_column(String(80))
    last_checked_at: Mapped[Optional[datetime]]
    revoked_at: Mapped[Optional[datetime]]


# ---------------------------------------------------------------- audit engine

VERDICTS = ("pass", "warn", "fail", "na", "not_implemented", "error")


class Scorecard(IdMixin, Base):
    __tablename__ = "scorecards"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    track: Mapped[str] = mapped_column(String(20))
    library_version: Mapped[str] = mapped_column(String(20))
    snapshot_ref: Mapped[str] = mapped_column(String(500))
    as_of: Mapped[Optional[datetime]]
    summary: Mapped[dict] = mapped_column(default=dict)
    created_by: Mapped[str] = mapped_column(String(80))
    results: Mapped[list["CheckResult"]] = relationship(back_populates="scorecard", cascade="all, delete-orphan")


class CheckResult(IdMixin, Base):
    __tablename__ = "check_results"
    __table_args__ = (UniqueConstraint("scorecard_id", "check_id"),)
    scorecard_id: Mapped[str] = mapped_column(ForeignKey("scorecards.id"), index=True)
    check_id: Mapped[str] = mapped_column(String(20))
    verdict: Mapped[str] = mapped_column(String(20))
    value: Mapped[dict] = mapped_column(default=dict)
    evidence: Mapped[list] = mapped_column(default=list)
    message: Mapped[str] = mapped_column(Text, default="")
    insight_id: Mapped[Optional[str]] = mapped_column(ForeignKey("insights.id"))
    scorecard: Mapped["Scorecard"] = relationship(back_populates="results")


# ---------------------------------------------------------------- audit collection (docs/AUDIT-PLAN.md section 4)

AUDIT_RUN_STATUSES = ("queued", "collecting", "scoring", "ready", "partial", "failed")


class AuditRun(IdMixin, Base):
    """One audit attempt: which collector stages ran, what each found, and where each stopped.

    stages: {stage_key: {status, started_at, finished_at, detail, counts, error, cursor}}, rewritten
    (never mutated in place) after every stage so readers and a resumed run see the latest state.
    """
    __tablename__ = "audit_runs"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    connection_id: Mapped[Optional[str]] = mapped_column(ForeignKey("connections.id"))
    account_id: Mapped[str] = mapped_column(String(100), default="")
    track: Mapped[str] = mapped_column(String(10), default="snapshot")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    stages: Mapped[dict] = mapped_column(default=dict)
    started_by: Mapped[str] = mapped_column(String(80))
    job_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("job_runs.id"))
    scorecard_id: Mapped[Optional[str]] = mapped_column(ForeignKey("scorecards.id"))
    started_at: Mapped[Optional[datetime]]
    finished_at: Mapped[Optional[datetime]]
    error: Mapped[str] = mapped_column(Text, default="")
    # Client-confirmed facts the account can't tell us (store_platform, ...), for metric choice and checks.
    context: Mapped[dict] = mapped_column(default=dict)


class AuditArtifact(IdMixin, Base):
    """One collector output (a snapshot.FILES key) for one run, in the reference collector's JSON shape."""
    __tablename__ = "audit_artifacts"
    __table_args__ = (UniqueConstraint("run_id", "name"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("audit_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(default=dict)
    captured_at: Mapped[Optional[datetime]]


class AuditFact(IdMixin, Base):
    """One measured number an analysis module produced for a run: the ledger every report section and
    narrative draft must quote from. value is {"v": ...}; v is None when the input was unavailable."""
    __tablename__ = "audit_facts"
    __table_args__ = (UniqueConstraint("run_id", "fact_id"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("audit_runs.id"), index=True)
    fact_id: Mapped[str] = mapped_column(String(120))
    module: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(300))
    value: Mapped[dict] = mapped_column(default=dict)
    unit: Mapped[str] = mapped_column(String(30), default="")
    window: Mapped[str] = mapped_column(String(60), default="")
    time_basis: Mapped[str] = mapped_column(String(10), default="event")
    source: Mapped[str] = mapped_column(String(300), default="")
    evidence_class: Mapped[str] = mapped_column(String(20), default="documented")


class WorkspaceBundle(IdMixin, Base):
    """One published version of a client's workspace (wavyos.workspace.Bundle), built from one audit run.

    status: measured (numbers only; written parts wait for strategist review) or reviewed.
    """
    __tablename__ = "workspace_bundles"
    __table_args__ = (UniqueConstraint("client_id", "version"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("audit_runs.id"), unique=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="measured")
    bundle: Mapped[dict] = mapped_column(default=dict)
    updated_at: Mapped[Optional[datetime]]


NARRATIVE_STATUSES = ("drafting", "draft", "needs_rewrite", "skipped", "approved", "edited", "rejected")


class NarrativeDraft(IdMixin, Base):
    """The written half of one workspace section for one audit run (wavyos.narrative).

    draft          passed the facts check and the critic; waiting in the strategist queue (approval_id)
    needs_rewrite  still failing after the last round; a strategist must edit it before it can publish
    skipped        not drafted (spend cap reached, no provider allowed); reason says why
    approved       published to the brand as drafted; edited: published after a strategist's edit (diff)
    rejected       never shown to the brand
    """
    __tablename__ = "narrative_drafts"
    __table_args__ = (UniqueConstraint("run_id", "section"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("audit_runs.id"), index=True)
    section: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="drafting", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(default=dict)
    original: Mapped[Optional[dict]]
    packet_hash: Mapped[str] = mapped_column(String(64), default="")
    facts_check: Mapped[dict] = mapped_column(default=dict)
    critic: Mapped[dict] = mapped_column(default=dict)       # {independent, rounds: [{scores, total, notes}]}
    score: Mapped[Optional[float]] = mapped_column(Float)
    rounds: Mapped[int] = mapped_column(Integer, default=0)
    drafter: Mapped[str] = mapped_column(String(80), default="")
    critic_by: Mapped[str] = mapped_column(String(80), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    approval_id: Mapped[Optional[str]] = mapped_column(String(32))
    reviewer: Mapped[Optional[str]] = mapped_column(String(120))
    reviewed_at: Mapped[Optional[datetime]]
    review_note: Mapped[str] = mapped_column(Text, default="")
    diff: Mapped[list] = mapped_column(default=list)
    updated_at: Mapped[Optional[datetime]]


OUTCOME_STATUSES = ("tracking", "improved", "flat", "worse", "unmeasured", "withdrawn")


class OpportunityOutcome(IdMixin, Base):
    """An approved opportunity and what happened after (wavyos.loop.outcomes).

    Approval records the baseline: the value of the card's readout fact in the run it was approved from.
    Each later scored run adds a readout; once the readout window has passed the outcome gets a verdict
    (improved / flat / worse, or unmeasured when no later run measured the fact), which feeds back into
    how the playbook behind the card (source_key) is ranked for every brand.
    """
    __tablename__ = "opportunity_outcomes"
    __table_args__ = (UniqueConstraint("client_id", "run_id", "opportunity_id"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("audit_runs.id"), index=True)
    opportunity_id: Mapped[str] = mapped_column(String(120))
    source_key: Mapped[str] = mapped_column(String(160), index=True)   # check id or analysis finding id
    title: Mapped[str] = mapped_column(String(300), default="")
    owner: Mapped[str] = mapped_column(String(120), default="")
    approved_by: Mapped[str] = mapped_column(String(120), default="")
    approved_at: Mapped[datetime]
    readout_metric: Mapped[str] = mapped_column(String(200), default="")
    readout_fact: Mapped[Optional[str]] = mapped_column(String(120))
    direction: Mapped[str] = mapped_column(String(4), default="up")     # up: higher is better; down: lower is
    baseline: Mapped[dict] = mapped_column(default=dict)                # {"v": value, "unit", "label"}
    readout_days: Mapped[int] = mapped_column(Integer, default=45)
    due_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(20), default="tracking", index=True)
    readouts: Mapped[list] = mapped_column(default=list)                # [{run_id, at, v, check}]
    verdict_reason: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[Optional[datetime]]
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[Optional[datetime]]


class AuditOrder(IdMixin, Base):
    """One Placed Order event, compact. Profile ids are Klaviyo's pseudonymous ids: never emails or names.

    Keyed by Klaviyo's event id, so a retried page or a re-audit upserts instead of duplicating.
    order_id is the store's id ($event_id or OrderId) that the engine dedupes on.
    """
    __tablename__ = "audit_orders"
    __table_args__ = (UniqueConstraint("client_id", "event_id"),
                      Index("ix_audit_orders_client_placed", "client_id", "placed_at"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    metric_id: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[Optional[str]] = mapped_column(String(120))
    profile_id: Mapped[Optional[str]] = mapped_column(String(64))
    placed_at: Mapped[datetime]
    value: Mapped[float] = mapped_column(Float, default=0.0)
    items: Mapped[list] = mapped_column(default=list)
    discount: Mapped[Optional[list]] = mapped_column()
    source: Mapped[Optional[str]] = mapped_column(String(120))


class KlaviyoReportCall(IdMixin, Base):
    """Reporting-endpoint calls per Klaviyo account per UTC day (Klaviyo allows 225, shared with the brand's other apps)."""
    __tablename__ = "klaviyo_report_calls"
    __table_args__ = (UniqueConstraint("account_id", "day"),)
    account_id: Mapped[str] = mapped_column(String(100))
    day: Mapped[str] = mapped_column(String(10))
    count: Mapped[int] = mapped_column(Integer, default=0)


class MetricPin(IdMixin, Base):
    """A staff-verified metric id for one event name on one client's account. Pins fail closed:
    a pinned id that's gone, renamed or from another integration stops the audit instead of guessing."""
    __tablename__ = "metric_pins"
    __table_args__ = (UniqueConstraint("client_id", "name"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    metric_id: Mapped[str] = mapped_column(String(64))
    integration: Mapped[Optional[str]] = mapped_column(String(120))
    created_by: Mapped[str] = mapped_column(String(80))


# ---------------------------------------------------------------- event log

class Event(Base):
    """Append-only. hash = sha256(prev_hash + canonical event body), chained per (org, client)."""
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("org_id", "chain", "seq"),)
    # 64-bit on Postgres; SQLite only autoincrements a column declared exactly INTEGER.
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[Optional[str]] = mapped_column(ForeignKey("clients.id"), index=True)
    chain: Mapped[str] = mapped_column(String(40))  # client_id, or "org" for org-level events
    seq: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(80))   # "user:<id>" | "agent:<id>" | "system"
    actor_role: Mapped[str] = mapped_column(String(30), default="")
    action: Mapped[str] = mapped_column(String(80))
    object_kind: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]


# ---------------------------------------------------------------- execution (task-level work by models and people)

EXECUTION_STATUSES = ("queued", "running", "waiting_approval", "succeeded", "failed", "cancelled")
EXECUTION_PURPOSES = ("produce", "qa")


class Execution(IdMixin, Base):
    """One attempt by one executor (a model or a person) to do one task, or to QA it."""
    __tablename__ = "executions"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(10), default="produce")
    executor: Mapped[str] = mapped_column(String(40))      # anthropic | codex | human | fake
    model: Mapped[str] = mapped_column(String(80), default="")
    agent_role: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    packet: Mapped[dict] = mapped_column(default=dict)     # the exact work packet the executor received
    packet_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(default=dict)     # {summary, outputs:[...], checks:[...]}
    error: Mapped[str] = mapped_column(Text, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    waiting_on: Mapped[Optional[str]] = mapped_column(ForeignKey("approvals.id"))
    started_at: Mapped[Optional[datetime]]
    finished_at: Mapped[Optional[datetime]]
    requested_by: Mapped[str] = mapped_column(String(80))


STEP_KINDS = ("model_call", "tool_call", "policy", "approval", "note", "error")


class ExecutionStep(IdMixin, Base):
    """The trace: every model call, tool call and policy decision inside an execution, in order."""
    __tablename__ = "execution_steps"
    __table_args__ = (UniqueConstraint("execution_id", "seq"),)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="ok")   # ok | denied | error | waiting
    input: Mapped[dict] = mapped_column(default=dict)
    output: Mapped[dict] = mapped_column(default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class Artifact(IdMixin, Base):
    """Something an execution produced: a brief, copy, a report. Content lives here, versioned per task."""
    __tablename__ = "artifacts"
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    execution_id: Mapped[Optional[str]] = mapped_column(ForeignKey("executions.id"))
    kind: Mapped[str] = mapped_column(String(40))      # brief | copy | design_direction | report | analysis | other
    label: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(80))


class ProviderCredential(IdMixin, Base):
    """An org-level model provider key (e.g. Anthropic). Sealed with the vault like client connections."""
    __tablename__ = "provider_credentials"
    __table_args__ = (UniqueConstraint("org_id", "provider"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_ref: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[str] = mapped_column(String(80))
