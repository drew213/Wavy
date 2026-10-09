"""Organization-level routes: identity, clients, standards, the policy catalog and job contracts."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from sqlalchemy import select

from .. import jobs, knowledge, policy, tenancy
from .. import models as m
from ..scope import user_roles
from . import schemas as s
from .deps import OrgScope, SessionDep, UserDep, require, resolve_client

router = APIRouter(prefix="/v1")


def load_jobmods() -> None:
    """Register the job handlers if the jobmods package is in this build."""
    try:
        import wavyos.jobmods  # noqa: F401
    except ModuleNotFoundError as e:
        if e.name != "wavyos.jobmods":
            raise


# ---------------------------------------------------------------- identity and clients

@router.get("/me", response_model=s.MeOut)
def me(session: SessionDep, user: UserDep):
    org = session.get(m.Organization, user.org_id)
    rows = session.execute(
        select(m.Membership.role, m.Membership.client_id, m.Client.slug)
        .outerjoin(m.Client, m.Client.id == m.Membership.client_id)
        .where(m.Membership.user_id == user.id).order_by(m.Membership.role)).all()
    return s.MeOut(id=user.id, email=user.email, name=user.name, kind=user.kind, org=s.OrgOut.model_validate(org),
                   memberships=[s.MembershipOut(role=r, client_id=c, client_slug=slug) for r, c, slug in rows])


@router.get("/clients", response_model=list[s.ClientOut])
def list_clients(session: SessionDep, user: UserDep):
    """Clients the caller holds a role on (every client, for agency-wide roles)."""
    clients = session.scalars(select(m.Client).where(m.Client.org_id == user.org_id).order_by(m.Client.slug)).all()
    return [s.ClientOut.model_validate(c) for c in clients if user_roles(session, user.id, c.id)]


@router.post("/clients", response_model=s.ClientOut, status_code=201)
def create_client(body: s.ClientIn, session: SessionDep, scope: OrgScope):
    c = tenancy.create_client(session, scope, name=body.name, slug=body.slug, timezone=body.timezone,
                              website=body.website)
    return s.ClientOut.model_validate(c)


# ---------------------------------------------------------------- standards (layer 1)

@router.get("/standards", response_model=list[s.StandardOut])
def list_standards(session: SessionDep, scope: OrgScope, area: Optional[str] = None):
    require(session, scope, "data.read")
    return [s.StandardOut.model_validate(x) for x in knowledge.standards(session, scope.org_id, area)]


@router.post("/standards", response_model=s.StandardOut, status_code=201)
def publish_standard(body: s.StandardIn, session: SessionDep, scope: OrgScope):
    st = knowledge.publish_standard(session, scope, body.key, body.area, body.component, body.title,
                                    body=body.body, data=body.data, source=body.source)
    session.flush()
    return s.StandardOut.model_validate(st)


# ---------------------------------------------------------------- policy

@router.get("/policy/actions", response_model=list[s.ActionOut])
def policy_actions(user: UserDep):
    return [s.ActionOut(key=a.key, level=a.level, description=a.description, performers=sorted(a.performers),
                        approvers=sorted(a.approvers), floor=a.floor, locked=a.key in policy.LOCKED,
                        human_only=a.human_only) for a in policy.ACTIONS.values()]


@router.post("/policy/overrides", response_model=s.OverrideOut, status_code=201)
def set_override(body: s.OverrideIn, session: SessionDep, scope: OrgScope, user: UserDep):
    client_id = resolve_client(session, user, body.client_slug).id if body.client_slug else None
    o = policy.set_override(session, scope, body.action, body.level, body.reason, client_id=client_id,
                            expires_at=body.expires_at)
    session.flush()
    return s.OverrideOut.model_validate(o)


# ---------------------------------------------------------------- jobs

@router.get("/jobs/definitions", response_model=list[s.JobDefinitionOut])
def job_definitions(user: UserDep):
    load_jobmods()
    return [s.JobDefinitionOut(key=d.key, title=d.title, cadence=d.cadence, owner_role=d.owner_role, checks=d.checks,
                               action=d.action, completion=d.completion, events=list(d.events), area=d.area,
                               implemented=d.handler is not None)
            for d in sorted(jobs.REGISTRY.values(), key=lambda d: d.key)]
