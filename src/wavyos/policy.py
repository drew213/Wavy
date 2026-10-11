"""The permission engine: what an actor may do, and what it needs first.

Every action is registered in ACTIONS with a default level:
  autonomous         do it and log it
  approved_plan      only inside an approved decision whose scope lists the action
  explicit_approval  each instance needs its own approval, bound to the exact payload

authorize() returns an Authorization and logs the outcome either way, except allowed reads: logging
every page view would put a write on the brand's event chain for each read. Denied reads are logged,
and opening a stored key logs its own connection.used event. Overrides can move an
action's level per org or per client, but never below its floor: LOCKED actions stay at
explicit_approval forever.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import events
from . import models as m
from .db import utcnow
from .scope import Scope

Level = Literal["autonomous", "approved_plan", "explicit_approval"]
LEVELS: tuple[Level, ...] = ("autonomous", "approved_plan", "explicit_approval")
Outcome = Literal["allow", "needs_plan", "needs_approval", "deny"]

STAFF = frozenset(m.STAFF_ROLES)
AGENTS = frozenset(m.AGENT_ROLES)
CLIENT_DECIDERS = frozenset({"client_owner", "client_approver"})
EVERYONE = STAFF | AGENTS | frozenset(m.CLIENT_ROLES)


@dataclass(frozen=True)
class Action:
    key: str
    level: Level
    description: str
    performers: frozenset[str] = STAFF | AGENTS     # roles allowed to attempt it
    approvers: frozenset[str] = frozenset()         # human roles allowed to approve it
    floor: Level = "autonomous"                     # overrides can't relax it below this
    human_only: bool = False                        # agents may never perform it


def _a(*args, **kw) -> Action:
    return Action(*args, **kw)


LEADS = frozenset({"org_admin", "account_lead"})
UNLOGGED_ALLOWS = frozenset({"data.read"})

ACTIONS: dict[str, Action] = {a.key: a for a in [
    # ---- autonomous
    _a("data.read", "autonomous", "Read client data and connected accounts", performers=EVERYONE),
    _a("analysis.run", "autonomous", "Analyze performance, run checks and audits"),
    _a("insight.create", "autonomous", "Record an insight with evidence"),
    _a("draft.create", "autonomous", "Draft briefs, copy, design direction and reports"),
    _a("task.manage", "autonomous", "Create and update internal tasks"),
    _a("issue.flag", "autonomous", "Flag an issue for a person"),
    _a("fact.propose", "autonomous", "Propose a client fact (it stays proposed until approved)",
       performers=STAFF | AGENTS | CLIENT_DECIDERS),
    _a("decision.propose", "autonomous", "Propose a decision from insights"),
    _a("approval.request", "autonomous", "Ask a person to approve something", performers=STAFF | AGENTS),
    _a("result.record", "autonomous", "Record a measured result once its window has closed"),
    _a("job.run", "autonomous", "Run a scheduled or triggered job"),
    _a("standard.publish", "autonomous", "Publish a new version of an agency standard",
       performers=frozenset({"org_admin"}), human_only=True),
    _a("connection.manage", "autonomous", "Add, check or revoke a connected account",
       performers=LEADS | {"client_owner"}, human_only=True),
    # ---- approved plan
    _a("campaign.build_draft", "approved_plan", "Build a draft campaign in the platform (not scheduled)"),
    _a("flow.build_draft", "approved_plan", "Build or edit a draft flow (not live)"),
    _a("audience.prepare", "approved_plan", "Prepare a segment or list change without applying it to sends"),
    _a("work.implement_nonlive", "approved_plan", "Implement agreed work in a non-live state"),
    # ---- explicit approval
    _a("fact.approve", "explicit_approval", "Make a proposed client fact part of the client's knowledge",
       approvers=LEADS | CLIENT_DECIDERS),
    _a("decision.approve", "explicit_approval", "Approve a decision and its plan scope",
       approvers=LEADS | CLIENT_DECIDERS),
    _a("client.message.send", "explicit_approval", "Send a communication to the client", approvers=LEADS),
    _a("campaign.publish", "explicit_approval", "Schedule or send a campaign", approvers=LEADS | CLIENT_DECIDERS),
    _a("flow.change_live", "explicit_approval", "Activate, pause or change a live flow", approvers=LEADS | CLIENT_DECIDERS),
    _a("discount.change", "explicit_approval", "Create or change a discount or offer",
       approvers=frozenset({"org_admin"}) | CLIENT_DECIDERS, floor="explicit_approval"),
    _a("profile.suppress_delete", "explicit_approval",
       "Discretionary suppression, deletion or bulk cleanup of profiles. Verified opt-outs are applied "
       "automatically by the platform and never wait for this approval",
       approvers=frozenset({"org_admin", "client_owner"}), floor="explicit_approval"),
    _a("account.settings_change", "explicit_approval", "Change platform account settings",
       approvers=frozenset({"org_admin", "client_owner"}), floor="explicit_approval"),
]}

LOCKED = frozenset(k for k, a in ACTIONS.items() if a.floor == "explicit_approval")


@dataclass
class Authorization:
    outcome: Outcome
    action: str
    level: Optional[Level]
    reason: str
    decision_id: Optional[str] = None
    approval_id: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def effective_level(session: Session, scope: Scope, action: Action) -> tuple[Level, str]:
    """Client override beats org override beats the default; never below the floor."""
    now = utcnow()
    rows = session.scalars(
        select(m.PolicyOverride).where(
            m.PolicyOverride.org_id == scope.org_id, m.PolicyOverride.action == action.key,
            (m.PolicyOverride.client_id.is_(None)) | (m.PolicyOverride.client_id == scope.client_id),
        )
    ).all()
    live = [r for r in rows if r.expires_at is None or _aware(r.expires_at) > now]
    chosen = next((r for r in live if r.client_id), None) or next((r for r in live if r.client_id is None), None)
    level: Level = chosen.level if chosen else action.level
    source = f"override {chosen.id}" if chosen else "default"
    if LEVELS.index(level) < LEVELS.index(action.floor):
        level, source = action.floor, f"floor ({action.key} is locked)"
    return level, source


def _aware(dt):
    from datetime import timezone
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def authorize(session: Session, scope: Scope, action_key: str, *, decision_id: Optional[str] = None,
              approval_id: Optional[str] = None, payload: Any = None, consume: bool = True,
              log: bool = True) -> Authorization:
    """Decide whether scope.actor may perform action_key now.

    approved_plan actions need decision_id; explicit_approval actions need approval_id and the
    exact payload that was approved. A matched approval is consumed (single use) unless consume=False.
    """
    auth = _decide(session, scope, action_key, decision_id, approval_id, payload, consume)
    if log and not (auth.allowed and action_key in UNLOGGED_ALLOWS):
        events.record(session, scope, "policy." + ("allow" if auth.allowed else auth.outcome),
                      auth.approval_id or auth.decision_id or action_key, auth.reason,
                      {"action": action_key, "level": auth.level}, object_kind="policy")
    return auth


def _decide(session, scope, key, decision_id, approval_id, payload, consume) -> Authorization:
    action = ACTIONS.get(key)
    if action is None:
        return Authorization("deny", key, None, f"{key} isn't a registered action")
    actor = scope.actor
    if actor.kind == "system":
        if action.level != "autonomous" or action.human_only:
            return Authorization("deny", key, action.level, "the system actor can only perform autonomous actions")
    else:
        if action.human_only and not actor.is_human:
            return Authorization("deny", key, action.level, f"{key} can only be done by a person")
        if not (actor.roles & action.performers):
            return Authorization("deny", key, action.level, f"none of your roles ({', '.join(sorted(actor.roles)) or 'none'}) can {key}")
    level, source = effective_level(session, scope, action)

    if level == "autonomous":
        return Authorization("allow", key, level, f"{key} is autonomous ({source})")

    if level == "approved_plan":
        if not decision_id:
            return Authorization("needs_plan", key, level, f"{key} needs an approved decision that covers it")
        d = session.get(m.Decision, decision_id)
        if d is None or d.org_id != scope.org_id or d.client_id != scope.client_id:
            return Authorization("needs_plan", key, level, "that decision isn't in this client workspace")
        if d.status != "approved":
            return Authorization("needs_plan", key, level, f"that decision is {d.status}, not approved")
        if key not in (d.scope or []):
            return Authorization("needs_plan", key, level, f"the approved decision doesn't include {key} in its scope")
        return Authorization("allow", key, level, f"covered by approved decision {d.id}", decision_id=d.id)

    # explicit approval
    if not approval_id:
        return Authorization("needs_approval", key, level, f"{key} needs its own approval")
    ap = session.get(m.Approval, approval_id)
    if ap is None or ap.org_id != scope.org_id or ap.client_id != scope.client_id:
        return Authorization("needs_approval", key, level, "that approval isn't in this client workspace")
    if ap.action != key:
        return Authorization("needs_approval", key, level, f"that approval is for {ap.action}, not {key}")
    if ap.status != "approved":
        return Authorization("needs_approval", key, level, f"that approval is {ap.status}")
    if ap.used_at is not None:
        return Authorization("needs_approval", key, level, "that approval was already used")
    if ap.expires_at is not None and _aware(ap.expires_at) <= utcnow():
        return Authorization("needs_approval", key, level, "that approval has expired")
    if payload_hash(payload) != ap.payload_hash:
        return Authorization("needs_approval", key, level, "the content changed after it was approved")
    if consume:
        ap.used_at = utcnow()
    return Authorization("allow", key, level, f"approved by {ap.decided_by}", approval_id=ap.id)


def set_override(session: Session, scope: Scope, action_key: str, level: Level, reason: str,
                 client_id: Optional[str], expires_at=None) -> m.PolicyOverride:
    """Org admins only. Locked actions can't be relaxed; the floor still applies at read time."""
    if not scope.actor.is_human or "org_admin" not in scope.actor.roles:
        from .scope import NotPermitted
        raise NotPermitted("only an org admin can change permission levels")
    action = ACTIONS.get(action_key)
    if action is None:
        raise ValueError(f"unknown action {action_key}")
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    if LEVELS.index(level) < LEVELS.index(action.floor):
        from .scope import NotPermitted
        raise NotPermitted(f"{action_key} is locked at {action.floor}")
    o = m.PolicyOverride(org_id=scope.org_id, client_id=client_id, action=action_key, level=level,
                         reason=reason, created_by=scope.actor.ref, expires_at=expires_at)
    session.add(o)
    events.record(session, Scope(scope.org_id, client_id, scope.actor), "policy.override", o,
                  f"{action_key} set to {level}: {reason}", {"action": action_key, "level": level})
    return o
