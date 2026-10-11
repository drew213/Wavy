"""client.onboard: the worker-side onboarding run.

The HTTP request only queues this job. The worker reads the client's Klaviyo account (orders,
flows, revenue) and, when one was connected, a bounded page of Shopify orders, then stores a new
dossier version. Each stage commits its progress so the onboarding page can show it live, and an
order import that runs out of page budget queues a follow-up run that resumes where it stopped.
"""
from __future__ import annotations

import uuid

from .. import connections, jobs, klaviyo_intake
from .. import models as m
from ..connectors.shopify import ShopifyClient
from ..jobs import JobContext, JobOutcome, job
from ..onboarding import ClientOnboardingService, OnboardingIntakePayload
from ..scope import scope_for_user
from ..storage import KLAVIYO, Progress, cohort_from_orders, ingest_shopify

KEY = "client.onboard"

STAGES = [
    ("connect", "Connect to Klaviyo"),
    ("orders", "Import 12 months of orders"),
    ("flows", "Read every flow"),
    ("performance", "Measure flow and campaign revenue"),
    ("analyze", "Analyze customers"),
    ("sizing", "Size the revenue leaks"),
    ("plan", "Write the 30-day plan"),
    ("build", "Draft the five core flows"),
    ("qa", "Compliance QA"),
    ("save", "Save the audit"),
]
KLAVIYO_STAGES = ("connect", "orders", "flows", "performance")


def _payload(raw: dict) -> tuple[OnboardingIntakePayload, str | None]:
    data = dict(raw or {})
    data.pop("shopify_access_token", None)
    data.pop("klaviyo_private_key", None)
    data.pop("continuation", None)
    requested_by = data.pop("requested_by", None)
    return OnboardingIntakePayload(**data), requested_by


def _human_scope(ctx: JobContext, requested_by: str | None):
    """The requesting lead's scope, so their onboarding approves its own plan. Anyone else (a brand
    that signed itself up) gets the agent's scope, and the plan waits for a lead."""
    if not requested_by:
        return ctx.scope
    user = ctx.session.get(m.User, requested_by)
    if user is None or user.org_id != ctx.scope.org_id:
        return ctx.scope
    scope = scope_for_user(ctx.session, user, ctx.scope.require_client())
    return scope if scope.actor.roles & {"org_admin", "account_lead"} else ctx.scope


def _klaviyo(ctx: JobContext, progress: Progress, platform: str | None = None):
    """(klaviyo section, cohort) from the client's connected Klaviyo account, or (None, None)."""
    vault, make_client = ctx.services.get("vault"), ctx.services.get("klaviyo_factory")
    conns = connections.active(ctx.session, ctx.scope, "klaviyo")
    if not conns or vault is None or make_client is None:
        for key in KLAVIYO_STAGES:
            progress(key, "skipped", "")
        progress("connect", "skipped", "No Klaviyo key connected: using benchmark data")
        return None, None
    conn = conns[0]
    with connections.secret(ctx.session, ctx.scope, vault, conn.id, "onboarding audit") as key:
        client = make_client(key)
        try:
            section = klaviyo_intake.run(ctx.session, ctx.scope, client, progress=progress,
                                         expect_account=conn.external_account_id, platform=platform)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    label = section["account"].get("name") or conn.external_account_id
    return section, cohort_from_orders(ctx.session, ctx.scope, label, source=KLAVIYO)


def _shopify_cohort(ctx: JobContext, payload: OnboardingIntakePayload):
    vault = ctx.services.get("vault")
    domain = payload.shopify_domain
    if not domain or vault is None:
        return None
    match = next((c for c in connections.active(ctx.session, ctx.scope, "shopify")
                  if c.external_account_id == domain), None)
    if match is None:
        return None
    with connections.secret(ctx.session, ctx.scope, vault, match.id, "shopify order ingest") as token:
        client = ShopifyClient(domain, token)
        ingest_shopify(ctx.session, ctx.scope, client)
    return cohort_from_orders(ctx.session, ctx.scope, domain)


def _continue_backfill(ctx: JobContext, raw: dict) -> None:
    jobs.emit(ctx.session, ctx.scope, KEY, {**raw, "continuation": True}, dedupe=uuid.uuid4().hex)


@job(KEY, "Onboard a client", "event", "account_lead", area="onboarding", events=("client.onboard",),
     checks="The intake on the job, plus the client's sealed Klaviyo key (and Shopify token, if one was given).",
     action="Read the Klaviyo account (orders, flows, revenue), run onboarding, and store a new dossier version.",
     completion="Done when a dossier row for this run is stored.")
def client_onboard(ctx: JobContext) -> JobOutcome:
    raw = (ctx.run.trigger or {}).get("payload") or {}
    payload, requested_by = _payload(raw)
    progress = Progress(ctx.session, ctx.scope, ctx.run.id, STAGES)
    klaviyo, klaviyo_cohort = _klaviyo(ctx, progress, (payload.intake or {}).get("store_platform"))
    shopify_cohort = _shopify_cohort(ctx, payload)
    cohort = shopify_cohort or klaviyo_cohort
    source = "shopify" if shopify_cohort else KLAVIYO if klaviyo_cohort else ""
    package = ClientOnboardingService(ctx.session, _human_scope(ctx, requested_by)).onboard_client(
        payload, shopify_data=cohort, job_run_id=ctx.run.id, klaviyo=klaviyo, orders_source=source,
        progress=progress)
    ctx.note("dossiers", package.dossier_id)
    orders = (klaviyo or {}).get("orders") or {}
    backfilling = not orders.get("backfill_complete", True) and orders.get("new_this_run", 0) > 0
    if backfilling:
        _continue_backfill(ctx, raw)
    progress("save", "done", f"Audit saved as dossier v{package.dossier_version}"
             + (". Older orders keep importing in the background" if backfilling else ""),
             dossier_version=package.dossier_version, health_score=package.overall_health_score)
    return JobOutcome(f"dossier v{package.dossier_version} for {package.client_slug}")
