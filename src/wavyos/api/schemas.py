"""Request and response models.

Response models list their fields explicitly, so a column added later (or a secret such as
connections.secret_ciphertext or api_tokens.token_hash) can't reach a client by accident.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- tenancy

class OrgOut(Out):
    id: str
    name: str
    slug: str


class MembershipOut(BaseModel):
    role: str
    client_id: Optional[str]
    client_slug: Optional[str]


class MeOut(BaseModel):
    id: str
    email: str
    name: str
    kind: str
    org: OrgOut
    memberships: list[MembershipOut]


class ClientIn(In):
    name: str = Field(min_length=1, max_length=200)
    slug: str
    timezone: str = "UTC"
    website: Optional[str] = None


class ClientOut(Out):
    id: str
    name: str
    slug: str
    status: str
    timezone: str
    website: Optional[str]


# ---------------------------------------------------------------- knowledge

class StandardIn(In):
    key: str = Field(min_length=1, max_length=120)
    area: str
    component: str
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    data: dict = Field(default_factory=dict)
    source: str = ""


class StandardOut(Out):
    id: str
    key: str
    version: int
    area: str
    component: str
    title: str
    body: str
    data: dict
    status: str
    source: str
    created_by: str
    created_at: datetime


class FactIn(In):
    area: str
    component: str
    key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=300)
    value: dict = Field(default_factory=dict)
    body: str = ""
    source_kind: str
    source_ref: str = ""
    review_by: Optional[date] = None


class FactOut(Out):
    id: str
    area: str
    component: str
    key: str
    title: str
    value: dict
    body: str
    status: str
    source_kind: str
    source_ref: str
    proposed_by: str
    approval_id: Optional[str]
    verified_by: Optional[str]
    verified_at: Optional[datetime]
    review_by: Optional[date]
    supersedes_id: Optional[str]
    created_at: datetime


class ApprovalOut(Out):
    id: str
    action: str
    subject_kind: str
    subject_id: str
    summary: str
    payload: dict
    payload_hash: str
    requested_by: str
    status: str
    decided_by: Optional[str]
    decided_at: Optional[datetime]
    expires_at: Optional[datetime]
    used_at: Optional[datetime]
    note: str
    created_at: datetime


class FactProposedOut(BaseModel):
    fact: FactOut
    approval: ApprovalOut


class DecideIn(In):
    approve: bool
    note: str = ""


# ---------------------------------------------------------------- operating chain

class Evidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: str = ""
    ref: str
    detail: str = ""


class InsightIn(In):
    area: str
    title: str = Field(min_length=1, max_length=300)
    evidence: list[Evidence]
    summary: str = ""
    severity: str = "medium"
    source_kind: str = "person"
    source_ref: str = ""
    dedupe_key: Optional[str] = None


class InsightOut(Out):
    id: str
    area: str
    title: str
    summary: str
    severity: str
    evidence: list
    source_kind: str
    source_ref: str
    dedupe_key: Optional[str]
    status: str
    created_by: str
    created_at: datetime


class InsightCreatedOut(BaseModel):
    insight: InsightOut
    created: bool


class DismissIn(In):
    reason: str


class Measurement(BaseModel):
    model_config = ConfigDict(extra="allow")
    metric: str
    window_days: int
    baseline: Optional[float] = None


class DecisionIn(In):
    title: str = Field(min_length=1, max_length=300)
    insight_ids: list[str]
    rationale: str = ""
    measurement: Measurement
    plan_scope: list[str] = Field(default_factory=list)


class DecisionOut(Out):
    id: str
    title: str
    rationale: str
    status: str
    scope: list
    measurement: dict
    proposed_by: str
    approval_id: Optional[str]
    outcome: str
    closed_at: Optional[datetime]
    created_at: datetime


class DecisionProposedOut(BaseModel):
    decision: DecisionOut
    approval: ApprovalOut


class TaskIn(In):
    title: str = Field(min_length=1, max_length=300)
    area: str
    owner_role: str
    done_when: str
    checklist: list[str]
    decision_id: Optional[str] = None
    job_run_id: Optional[str] = None
    due_date: Optional[date] = None
    assignee_id: Optional[str] = None


class TaskOut(Out):
    id: str
    decision_id: Optional[str]
    job_run_id: Optional[str]
    title: str
    area: str
    owner_role: str
    assignee_id: Optional[str]
    due_date: Optional[date]
    done_when: str
    status: str
    checklist: list
    outputs: list
    created_by: str
    completed_at: Optional[datetime]
    created_at: datetime


class TaskStatusIn(In):
    status: str
    note: str = ""


class CheckIn(In):
    passed: bool = True


class Output(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: str = ""
    ref: str
    label: str = ""


class CompleteIn(In):
    outputs: list[Output]


class ResultOut(Out):
    id: str
    task_id: str
    metric: str
    baseline: Optional[float]
    window_start: date
    window_end: date
    observed: Optional[float]
    status: str
    notes: str
    measured_by: Optional[str]
    measured_at: Optional[datetime]
    created_at: datetime


class TaskCompletedOut(BaseModel):
    task: TaskOut
    result: Optional[ResultOut]


class ResultIn(In):
    observed: Optional[float] = None
    notes: str
    inconclusive: bool = False


# ---------------------------------------------------------------- connections

class ConnectionIn(In):
    provider: str
    api_key: SecretStr  # masked in reprs and validation errors
    label: str = ""


class ConnectionOut(Out):
    id: str
    provider: str
    external_account_id: str
    status: str


# ---------------------------------------------------------------- policy

class ActionOut(BaseModel):
    key: str
    level: str
    description: str
    performers: list[str]
    approvers: list[str]
    floor: str
    locked: bool
    human_only: bool


class OverrideIn(In):
    action: str
    level: Literal["autonomous", "approved_plan", "explicit_approval"]
    reason: str = Field(min_length=1)
    client_slug: Optional[str] = None
    expires_at: Optional[datetime] = None


class OverrideOut(Out):
    id: str
    client_id: Optional[str]
    action: str
    level: str
    reason: str
    created_by: str
    expires_at: Optional[datetime]
    created_at: datetime


class AuthorizeIn(In):
    action: str
    decision_id: Optional[str] = None
    approval_id: Optional[str] = None
    payload: Any = None


class AuthorizeOut(BaseModel):
    outcome: str
    action: str
    level: Optional[str]
    reason: str
    decision_id: Optional[str]
    approval_id: Optional[str]
    dry_run: bool = True


# ---------------------------------------------------------------- jobs

class JobDefinitionOut(BaseModel):
    key: str
    title: str
    cadence: str
    owner_role: str
    checks: str
    action: str
    completion: str
    events: list[str]
    area: str
    implemented: bool


class JobRunOut(Out):
    id: str
    job_key: str
    run_key: str
    due_at: datetime
    trigger: dict
    status: str
    attempts: int
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    summary: str
    produced: dict
    cost_usd: Optional[float]
    created_at: datetime


# ---------------------------------------------------------------- audits

class AuditIn(In):
    snapshot_slug: str
    track: Literal["snapshot", "deep"] = "snapshot"


class CheckResultOut(Out):
    check_id: str
    verdict: str
    value: dict
    evidence: list
    message: str
    insight_id: Optional[str]


class ScorecardOut(Out):
    id: str
    track: str
    library_version: str
    snapshot_ref: str
    as_of: Optional[datetime]
    summary: dict
    created_by: str
    created_at: datetime


class ScorecardDetailOut(ScorecardOut):
    results: list[CheckResultOut]


# ---------------------------------------------------------------- events

class EventOut(Out):
    seq: int
    actor: str
    actor_role: str
    action: str
    object_kind: str
    object_id: str
    summary: str
    data: dict
    created_at: datetime
    hash: str


class ChainOut(BaseModel):
    ok: bool
    broken_at: Optional[int]
    count: int
