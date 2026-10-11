"""Ops API behind the console: what's running, where every client's work is, and the full trace of
every execution. Read-mostly; the few writes (dispatch, cancel, decide) go through the services.

Staff with org-wide roles see every client; client stakeholders see only their own clients.
"""
from __future__ import annotations

from collections import Counter
from typing import Callable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .. import approvals as approvals_svc
from .. import models as m
from .. import tenancy
from ..db import utcnow
from ..execution import engine
from ..jobs import REGISTRY
from ..ratelimit import Limiter
from ..scope import InvalidState, NotFound, NotPermitted, scope_for_user


# Request bodies live at module level: with postponed annotations FastAPI can only resolve
# models it can see here, and would otherwise read them as query parameters.
class DispatchBody(BaseModel):
    executor: Optional[str] = None


class DecideBody(BaseModel):
    approve: bool
    note: str = ""


class KlaviyoCheckBody(BaseModel):
    api_key: SecretStr


class SignupBody(BaseModel):
    brand_name: str
    name: str
    email: str
    password: SecretStr
    website: Optional[str] = None


class LoginBody(BaseModel):
    email: str
    password: SecretStr


class OnboardBody(BaseModel):
    client_name: str
    client_slug: str
    store_platform: str = "Shopify"
    website: str = "https://example.com"
    timezone_name: str = "America/New_York"
    primary_goal_90d: str = "Scale email revenue attribution to 30%+"
    messaging_tools: list[str] = ["Klaviyo"]
    subscription_app: str = "none"
    regulated_limits: str = "none"
    gross_margin_pct: float = 65.0
    free_shipping_threshold: float = 65.0
    target_aov: float = 80.0
    typical_consumption_days: int = 35
    discount_rules: str = "MODERATE: Welcome 15%, cart 10%, no stackable coupons"
    voice_archetype: str = "The Trusted Dermatologist Best Friend"
    tone_descriptors: list[str] = ["elevated", "approachable", "science-backed", "reassuring"]
    forbidden_words: list[str] = ["miracle", "cure", "cheap", "overnight fix", "toxic"]
    hero_products: list[dict[str, str]] = [
        {"name": "Barrier Recovery Complex", "sku": "SKU-BAR-01", "benefit": "Barrier hydration & repair"}
    ]
    shopify_domain: Optional[str] = None
    shopify_access_token: Optional[str] = None
    klaviyo_private_key: Optional[str] = None
    use_intake: bool = False  # build from the saved questionnaire answers; they must be complete


class IntakeBody(BaseModel):
    client_slug: str
    client_name: Optional[str] = None
    answers: dict


def make_ops_router(get_factory: Callable[[], sessionmaker]) -> APIRouter:
    r = APIRouter(prefix="/v1/ops", tags=["ops"])

    def db():
        s = get_factory()()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def user(authorization: Optional[str] = Header(default=None), s: Session = Depends(db)) -> m.User:
        token = (authorization or "").removeprefix("Bearer ").strip()
        u = tenancy.user_for_token(s, token)
        if u is None:
            raise HTTPException(401, {"error": "sign in with an API token"})
        return u

    def org_wide(s: Session, u: m.User) -> bool:
        return bool(s.scalar(select(func.count()).select_from(m.Membership).where(
            m.Membership.user_id == u.id, m.Membership.client_id.is_(None))))

    def visible_clients(s: Session, u: m.User) -> list[m.Client]:
        q = select(m.Client).where(m.Client.org_id == u.org_id)
        if not org_wide(s, u):
            ids = s.scalars(select(m.Membership.client_id).where(m.Membership.user_id == u.id)).all()
            q = q.where(m.Client.id.in_(ids))
        return list(s.scalars(q.order_by(m.Client.name)))

    def client_by_slug(s, u, slug) -> m.Client:
        c = next((c for c in visible_clients(s, u) if c.slug == slug), None)
        if c is None:
            raise HTTPException(404, {"error": "client not found"})
        return c

    def guard(fn):
        try:
            return fn()
        except NotPermitted as e:
            raise HTTPException(403, {"error": str(e)})
        except NotFound as e:
            raise HTTPException(404, {"error": str(e)})
        except InvalidState as e:
            raise HTTPException(409, {"error": str(e)})
        except ValueError as e:
            raise HTTPException(422, {"error": str(e)})

    # ---------------------------------------------------------------- overview

    @r.get("/overview")
    def overview(u: m.User = Depends(user), s: Session = Depends(db)):
        out = []
        for c in visible_clients(s, u):
            def count(model, *conds):
                return s.scalar(select(func.count()).select_from(model).where(model.client_id == c.id, *conds)) or 0
            tasks = Counter(dict(s.execute(select(m.Task.status, func.count()).where(m.Task.client_id == c.id).group_by(m.Task.status)).all()))
            decisions = Counter(dict(s.execute(select(m.Decision.status, func.count()).where(m.Decision.client_id == c.id).group_by(m.Decision.status)).all()))
            out.append({
                "slug": c.slug, "name": c.name, "status": c.status, "timezone": c.timezone,
                "providers": engine.allowed_providers(c),
                "insights_open": count(m.Insight, m.Insight.status == "open"),
                "decisions": dict(decisions), "tasks": dict(tasks),
                "results_pending": count(m.Result, m.Result.status == "pending"),
                "approvals_pending": count(m.Approval, m.Approval.status == "pending"),
                "executions": dict(Counter(dict(s.execute(select(m.Execution.status, func.count()).where(
                    m.Execution.client_id == c.id).group_by(m.Execution.status)).all()))),
                "spend_today_usd": round(engine.spent_today(s, c.id), 4),
                "budget_usd": float((c.settings or {}).get("daily_budget_usd", engine.DEFAULT_DAILY_BUDGET_USD)),
            })
        return {"now": utcnow().isoformat(), "clients": out}

    # ---------------------------------------------------------------- the chain board

    @r.get("/clients/{slug}/flow")
    def flow(slug: str, u: m.User = Depends(user), s: Session = Depends(db)):
        c = client_by_slug(s, u, slug)
        latest = {}
        for ex in s.scalars(select(m.Execution).where(m.Execution.client_id == c.id).order_by(m.Execution.created_at)):
            latest[ex.task_id] = ex
        results = {r_.task_id: r_ for r_ in s.scalars(select(m.Result).where(m.Result.client_id == c.id))}

        def task_card(t: m.Task):
            ex = latest.get(t.id)
            res = results.get(t.id)
            return {"id": t.id, "title": t.title, "status": t.status, "area": t.area, "owner_role": t.owner_role,
                    "due_date": t.due_date.isoformat() if t.due_date else None,
                    "checks": [{"text": i["text"], "passed": i["passed"]} for i in t.checklist],
                    "outputs": t.outputs,
                    "execution": ({"id": ex.id, "status": ex.status, "executor": ex.executor, "model": ex.model,
                                   "purpose": ex.purpose, "attempt": ex.attempt} if ex else None),
                    "result": ({"metric": res.metric, "status": res.status, "window_end": res.window_end.isoformat(),
                                "baseline": res.baseline, "observed": res.observed} if res else None)}

        decisions = []
        for d in s.scalars(select(m.Decision).where(m.Decision.client_id == c.id).order_by(m.Decision.created_at.desc())):
            decisions.append({"id": d.id, "title": d.title, "status": d.status, "measurement": d.measurement,
                              "scope": d.scope, "outcome": d.outcome,
                              "insights": [{"id": i.id, "title": i.title, "severity": i.severity} for i in d.insights],
                              "tasks": [task_card(t) for t in s.scalars(select(m.Task).where(m.Task.decision_id == d.id).order_by(m.Task.created_at))]})
        linked = {i["id"] for d in decisions for i in d["insights"]}
        loose = [{"id": i.id, "title": i.title, "severity": i.severity, "area": i.area, "source": i.source_kind,
                  "created_at": i.created_at.isoformat()}
                 for i in s.scalars(select(m.Insight).where(m.Insight.client_id == c.id, m.Insight.status == "open")
                                    .order_by(m.Insight.created_at.desc()).limit(50)) if i.id not in linked]
        standing = [task_card(t) for t in s.scalars(select(m.Task).where(m.Task.client_id == c.id, m.Task.job_run_id.is_not(None),
                                                                         m.Task.status.not_in(("done", "cancelled"))))]
        return {"client": {"slug": c.slug, "name": c.name}, "open_insights": loose, "decisions": decisions, "standing_tasks": standing}

    # ---------------------------------------------------------------- executions

    def ex_row(ex: m.Execution, c: m.Client, task_title: str):
        return {"id": ex.id, "client": c.slug, "task_id": ex.task_id, "task": task_title, "purpose": ex.purpose,
                "executor": ex.executor, "model": ex.model, "role": ex.agent_role, "status": ex.status,
                "attempt": ex.attempt, "cost_usd": ex.cost_usd, "input_tokens": ex.input_tokens,
                "output_tokens": ex.output_tokens, "created_at": ex.created_at.isoformat(),
                "started_at": ex.started_at.isoformat() if ex.started_at else None,
                "finished_at": ex.finished_at.isoformat() if ex.finished_at else None,
                "summary": (ex.result or {}).get("summary", ""), "error": ex.error.split("\n")[0][:300]}

    @r.get("/executions")
    def executions(client: Optional[str] = None, status: Optional[str] = None, limit: int = Query(50, le=200),
                   u: m.User = Depends(user), s: Session = Depends(db)):
        cs = {c.id: c for c in visible_clients(s, u)}
        if client:
            cs = {k: v for k, v in cs.items() if v.slug == client}
        q = select(m.Execution, m.Task.title).join(m.Task, m.Task.id == m.Execution.task_id).where(m.Execution.client_id.in_(list(cs)))
        if status:
            q = q.where(m.Execution.status == status)
        rows = s.execute(q.order_by(m.Execution.created_at.desc()).limit(limit)).all()
        return {"executions": [ex_row(ex, cs[ex.client_id], title) for ex, title in rows]}

    @r.get("/executions/{ex_id}")
    def execution(ex_id: str, u: m.User = Depends(user), s: Session = Depends(db)):
        cs = {c.id: c for c in visible_clients(s, u)}
        ex = s.get(m.Execution, ex_id)
        if ex is None or ex.client_id not in cs:
            raise HTTPException(404, {"error": "execution not found"})
        task = s.get(m.Task, ex.task_id)
        steps = s.scalars(select(m.ExecutionStep).where(m.ExecutionStep.execution_id == ex.id).order_by(m.ExecutionStep.seq)).all()
        arts = s.scalars(select(m.Artifact).where(m.Artifact.execution_id == ex.id).order_by(m.Artifact.created_at)).all()
        return {**ex_row(ex, cs[ex.client_id], task.title), "packet": ex.packet, "packet_hash": ex.packet_hash,
                "result": ex.result, "error_full": ex.error, "waiting_on": ex.waiting_on,
                "steps": [{"seq": st.seq, "kind": st.kind, "name": st.name, "status": st.status, "input": st.input,
                           "output": st.output, "input_tokens": st.input_tokens, "output_tokens": st.output_tokens,
                           "cost_usd": st.cost_usd, "duration_ms": st.duration_ms, "at": st.created_at.isoformat()} for st in steps],
                "artifacts": [{"id": a.id, "kind": a.kind, "label": a.label, "content": a.content} for a in arts]}

    @r.post("/clients/{slug}/tasks/{task_id}/dispatch")
    def dispatch(slug: str, task_id: str, body: DispatchBody, u: m.User = Depends(user), s: Session = Depends(db)):
        c = client_by_slug(s, u, slug)
        def go():
            ex = engine.dispatch(s, scope_for_user(s, u, c.id), task_id, executor=body.executor)
            s.flush()
            return {"execution_id": ex.id, "executor": ex.executor, "model": ex.model}
        return guard(go)

    @r.post("/executions/{ex_id}/cancel")
    def cancel(ex_id: str, u: m.User = Depends(user), s: Session = Depends(db)):
        cs = {c.id: c for c in visible_clients(s, u)}
        ex = s.get(m.Execution, ex_id)
        if ex is None or ex.client_id not in cs:
            raise HTTPException(404, {"error": "execution not found"})
        if ex.status not in ("queued", "waiting_approval"):
            raise HTTPException(409, {"error": f"can't cancel a {ex.status} execution"})
        from .. import events
        sc = guard(lambda: scope_for_user(s, u, ex.client_id))
        if not sc.actor.roles & {"org_admin", "account_lead"}:
            raise HTTPException(403, {"error": "only an org admin or account lead can cancel work"})
        ex.status, ex.finished_at = "cancelled", utcnow()
        events.record(s, sc, "execution.cancelled", ex, "cancelled from the console")
        return {"ok": True}

    # ---------------------------------------------------------------- jobs, approvals, events

    @r.get("/jobs")
    def jobs(limit: int = Query(50, le=200), u: m.User = Depends(user), s: Session = Depends(db)):
        cs = {c.id: c.slug for c in visible_clients(s, u)}
        runs = s.scalars(select(m.JobRun).where(m.JobRun.client_id.in_(list(cs))).order_by(m.JobRun.created_at.desc()).limit(limit)).all()
        return {
            "definitions": [{"key": d.key, "title": d.title, "cadence": d.cadence, "owner_role": d.owner_role,
                             "checks": d.checks, "action": d.action, "completion": d.completion,
                             "events": list(d.events), "implemented": d.handler is not None} for d in REGISTRY.values()],
            "runs": [{"id": j.id, "client": cs[j.client_id], "job": j.job_key, "status": j.status, "summary": j.summary,
                      "produced": j.produced, "error": j.error.split("\n")[0][:300], "due_at": j.due_at.isoformat(),
                      "finished_at": j.finished_at.isoformat() if j.finished_at else None} for j in runs],
        }

    @r.get("/approvals")
    def approvals(u: m.User = Depends(user), s: Session = Depends(db)):
        out = []
        for c in visible_clients(s, u):
            for a in s.scalars(select(m.Approval).where(m.Approval.client_id == c.id, m.Approval.status == "pending").order_by(m.Approval.created_at)):
                out.append({"id": a.id, "client": c.slug, "action": a.action, "summary": a.summary, "payload": a.payload,
                            "subject": f"{a.subject_kind}:{a.subject_id}", "requested_by": a.requested_by,
                            "created_at": a.created_at.isoformat(), "expires_at": a.expires_at.isoformat() if a.expires_at else None})
        return {"approvals": out}

    @r.post("/approvals/{ap_id}/decide")
    def decide(ap_id: str, body: DecideBody, u: m.User = Depends(user), s: Session = Depends(db)):
        ap = s.get(m.Approval, ap_id)
        if ap is None or ap.client_id not in {c.id for c in visible_clients(s, u)}:
            raise HTTPException(404, {"error": "approval not found"})
        return guard(lambda: {"status": approvals_svc.decide(s, scope_for_user(s, u, ap.client_id), ap_id, body.approve, body.note).status})

    def verify_klaviyo(request: Request, api_key: str) -> dict:
        from ..api.deps import service
        try:
            account = service(request, "klaviyo_verify")(api_key.strip()) or {}
        except Exception as e:  # never echo the provider's message: it may quote the key
            status = getattr(e, "status", None)
            reason = ("Klaviyo rejected this key. Create a private key with read-only access and paste it again."
                      if status in (401, 403) else f"Klaviyo couldn't be reached ({type(e).__name__}). Try again.")
            raise HTTPException(422, {"error": reason}) from None
        if not account.get("id"):
            raise HTTPException(422, {"error": "Klaviyo accepted the key but returned no account."})
        return account

    @r.post("/onboard/klaviyo-check")
    def klaviyo_check(body: KlaviyoCheckBody, request: Request, u: m.User = Depends(user)):
        account = verify_klaviyo(request, body.api_key.get_secret_value())
        return {"account_id": account["id"], "name": account.get("name"), "timezone": account.get("timezone")}

    @r.post("/onboard")
    def onboard_account(body: OnboardBody, request: Request, u: m.User = Depends(user), s: Session = Depends(db)):
        from ..onboarding import OnboardingIntakePayload, queue_onboarding
        klaviyo_account = verify_klaviyo(request, body.klaviyo_private_key) if body.klaviyo_private_key else None
        org = s.get(m.Organization, u.org_id)
        payload = OnboardingIntakePayload(
            org_slug=org.slug if org else "wavy",
            client_name=body.client_name,
            client_slug=body.client_slug,
            store_platform=body.store_platform,
            website=body.website,
            timezone_name=body.timezone_name,
            primary_goal_90d=body.primary_goal_90d,
            messaging_tools=body.messaging_tools,
            subscription_app=body.subscription_app,
            regulated_limits=body.regulated_limits,
            gross_margin_pct=body.gross_margin_pct,
            free_shipping_threshold=body.free_shipping_threshold,
            target_aov=body.target_aov,
            typical_consumption_days=body.typical_consumption_days,
            discount_rules=body.discount_rules,
            voice_archetype=body.voice_archetype,
            tone_descriptors=body.tone_descriptors,
            forbidden_words=body.forbidden_words,
            hero_products=body.hero_products,
            shopify_domain=body.shopify_domain,
            shopify_access_token=body.shopify_access_token,
            klaviyo_private_key=body.klaviyo_private_key,
        )
        vault = None
        if body.shopify_access_token or body.klaviyo_private_key:
            from ..api.deps import service
            vault = service(request, "vault")

        def _queue():
            nonlocal payload
            if org_wide(s, u):
                scope = scope_for_user(s, u, None)
            else:  # a brand onboards only its own workspace
                scope = scope_for_user(s, u, client_by_slug(s, u, body.client_slug).id)
            if body.use_intake:
                from dataclasses import replace
                from .. import intake
                row = intake_row(s, client_by_slug(s, u, body.client_slug))
                answers = row.answers if row else {}
                missing = intake.missing_required(answers)
                if missing:
                    raise ValueError("finish the onboarding questions first: " + "; ".join(missing))
                payload = replace(payload, **intake.payload_fields(answers))
                row.submitted_at = utcnow()
            run = queue_onboarding(s, scope, payload, vault, klaviyo_account)
            return {"status": "queued", "job_run_id": run.id, "client_id": run.client_id, "client_slug": body.client_slug}

        return guard(_queue)

    @r.get("/onboard/{run_id}")
    def onboard_status(run_id: str, u: m.User = Depends(user), s: Session = Depends(db)):
        from ..storage import progress_for
        run = s.get(m.JobRun, run_id)
        visible = {c.id: c for c in visible_clients(s, u)}
        if run is None or run.client_id not in visible:
            raise HTTPException(404, {"error": "onboarding run not found"})
        row = s.scalar(select(m.Dossier).where(m.Dossier.job_run_id == run.id))
        ready = row is not None and run.status == "succeeded"
        return {
            "status": run.status,
            "job_run_id": run.id,
            "client_slug": visible[run.client_id].slug,
            "summary": run.summary,
            "error": run.error.split("\n", 1)[0] if run.error else "",
            "progress": progress_for(s, run.id),
            "dossier_id": row.id if ready else None,
            "dossier_version": row.version if ready else None,
            "result": row.document if ready else None,
        }

    @r.get("/events")
    def events_tail(after: int = 0, client: Optional[str] = None, limit: int = Query(100, le=500),
                    u: m.User = Depends(user), s: Session = Depends(db)):
        cs = {c.id: c.slug for c in visible_clients(s, u)}
        if client:
            cs = {k: v for k, v in cs.items() if v == client}
        q = select(m.Event).where(m.Event.org_id == u.org_id, m.Event.id > after, m.Event.client_id.in_(list(cs)))
        rows = s.scalars(q.order_by(m.Event.id.desc()).limit(limit)).all()
        return {"events": [{"id": e.id, "client": cs.get(e.client_id), "seq": e.seq, "actor": e.actor, "role": e.actor_role,
                            "action": e.action, "object": f"{e.object_kind}:{e.object_id}", "summary": e.summary,
                            "at": e.created_at.isoformat(), "hash": e.hash[:12]} for e in reversed(rows)]}

    @r.get("/me")
    def me(u: m.User = Depends(user), s: Session = Depends(db)):
        from ..storage import latest_dossier
        roles = s.execute(select(m.Membership.role, m.Membership.client_id).where(m.Membership.user_id == u.id)).all()
        clients = [] if org_wide(s, u) else [
            {"slug": c.slug, "name": c.name, "has_audit": latest_dossier(s, c.id) is not None}
            for c in visible_clients(s, u)]
        return {"name": u.name, "email": u.email, "kind": u.kind, "staff": org_wide(s, u), "clients": clients,
                "roles": [{"role": r_, "client_id": cid} for r_, cid in roles]}

    # ---------------------------------------------------------------- brand accounts and saved audits

    signup_limit = Limiter(5, 3600)
    login_by_ip, login_by_email = Limiter(20, 900), Limiter(10, 900)

    def too_many() -> HTTPException:
        return HTTPException(429, {"error": "Too many attempts. Wait a few minutes and try again."})

    def visitor(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @r.post("/signup")
    def signup(body: SignupBody, request: Request, s: Session = Depends(db)):
        from .. import accounts
        from ..api.deps import service
        if not signup_limit.allow(visitor(request)):
            raise too_many()

        def _create():
            org = accounts.signup_org(s, service(request, "settings").signup_org)
            client, _, token = accounts.signup_brand(
                s, org, brand_name=body.brand_name, person_name=body.name, email=body.email,
                password=body.password.get_secret_value(), website=body.website)
            return {"token": token, "client_slug": client.slug, "client_name": client.name}
        return guard(_create)

    @r.post("/login")
    def login(body: LoginBody, request: Request, s: Session = Depends(db)):
        from .. import accounts
        if not (login_by_ip.allow(visitor(request)) and login_by_email.allow(body.email.strip().lower())):
            raise too_many()
        try:
            _, token = accounts.sign_in(s, body.email, body.password.get_secret_value())
        except accounts.SignInFailed as e:
            raise HTTPException(401, {"error": str(e)})
        return {"token": token}

    def intake_row(s: Session, c: m.Client) -> Optional[m.OnboardingIntake]:
        return s.scalar(select(m.OnboardingIntake).where(m.OnboardingIntake.client_id == c.id))

    def intake_view(c: Optional[m.Client], row: Optional[m.OnboardingIntake]) -> dict:
        from .. import intake
        answers = row.answers if row else {}
        return {"client_slug": c.slug if c else None, "client_name": c.name if c else None,
                "sections": intake.SECTIONS, "answers": answers, **intake.progress(answers),
                "missing": intake.missing_required(answers),
                "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
                "submitted_at": row.submitted_at.isoformat() if row and row.submitted_at else None}

    @r.get("/intake")
    def get_intake(client: Optional[str] = None, u: m.User = Depends(user), s: Session = Depends(db)):
        """The questionnaire, plus this client's saved answers when there is a client."""
        c = client_by_slug(s, u, client) if client else None
        return intake_view(c, intake_row(s, c) if c else None)

    @r.put("/intake")
    def save_intake(body: IntakeBody, u: m.User = Depends(user), s: Session = Depends(db)):
        """Autosave. Staff can start a new client's questionnaire; it creates the workspace."""
        from .. import intake

        def _save():
            c = next((x for x in visible_clients(s, u) if x.slug == body.client_slug), None)
            if c is None:
                if not org_wide(s, u) or not (body.client_name or "").strip():
                    raise HTTPException(404, {"error": "client not found"})
                c = tenancy.create_client(s, scope_for_user(s, u, None), name=body.client_name.strip(), slug=body.client_slug)
            answers = intake.clean(body.answers)
            row = intake_row(s, c)
            if row is None:
                row = m.OnboardingIntake(org_id=c.org_id, client_id=c.id, answers={})
                s.add(row)
            row.answers, row.updated_by, row.updated_at = answers, u.id, utcnow()
            s.flush()
            return intake_view(c, row)
        return guard(_save)

    @r.get("/clients/{slug}/audit")
    def saved_audit(slug: str, version: Optional[int] = None, u: m.User = Depends(user), s: Session = Depends(db)):
        c = client_by_slug(s, u, slug)
        rows = s.scalars(select(m.Dossier).where(m.Dossier.client_id == c.id).order_by(m.Dossier.version.desc())).all()
        if not rows:
            raise HTTPException(404, {"error": "no audit yet for this brand"})
        row = next((d for d in rows if d.version == version), None) if version else rows[0]
        if row is None:
            raise HTTPException(404, {"error": f"there is no version {version}"})
        return {
            "client_slug": c.slug, "client_name": c.name, "version": row.version,
            "created_at": row.created_at.isoformat(), "result": row.document,
            "versions": [{"version": d.version, "created_at": d.created_at.isoformat(),
                          "health_score": (d.document or {}).get("overall_health_score")} for d in rows],
        }

    return r
