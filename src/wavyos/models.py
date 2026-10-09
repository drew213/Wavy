"""All tables. Every client-owned row carries org_id and client_id.

Status values are plain strings checked by the services (see the *_STATUSES tuples),
so the same schema works on SQLite and Postgres without native enums.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, Date, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
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
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    job_key: Mapped[str] = mapped_column(String(80), index=True)
    run_key: Mapped[str] = mapped_column(String(200), unique=True)
    due_at: Mapped[datetime]
    trigger: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[Optional[datetime]]
    finished_at: Mapped[Optional[datetime]]
    summary: Mapped[str] = mapped_column(Text, default="")
    produced: Mapped[dict] = mapped_column(default=dict)   # {insights: [], tasks: [], approvals: []}
    error: Mapped[str] = mapped_column(Text, default="")
    cost_usd: Mapped[Optional[float]] = mapped_column(Float)


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


# ---------------------------------------------------------------- event log

class Event(Base):
    """Append-only. hash = sha256(prev_hash + canonical event body), chained per (org, client)."""
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("org_id", "chain", "seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
