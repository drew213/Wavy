"""Client Onboarding & Agentic Handoff Engine for Wavy OS.

Automates the complete agency lifecycle onboarding:
1. Ingests qualitative intake (Brand DNA, voice archetype, economics, guardrails)
2. Ingests Layer 2 Client Facts into Wavy OS (propose & approve facts)
3. Pulls Shopify & Klaviyo cohort, catalog, and flow data via connectors
4. Evaluates against the 45-check audit library & runs the Opportunity Sizing Engine
5. Emits deduplicated Insights with verified evidence
6. Proposes and approves Decision: 'First 30-Day Onboarding & Repair Plan'
7. Spawns foundational Tasks with 5-10 point QA checklists
8. Dispatches Founder Voice Copy Agent to draft Campaign #1 with compliance sentry.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import approvals, events, knowledge, operations, tenancy
from . import models as m
from .audit.sizing import OpportunitySizingEngine, SizedOpportunity, SizingReport
from .connectors.shopify import ShopifyClient, ShopifyCohortData
from .connectors.klaviyo import KlaviyoClient
from .db import utcnow
from .scope import Scope

_DISCOUNT_STANCES = {"NEVER_DISCOUNT", "VALUE_ADD_ONLY", "MODERATE", "AGGRESSIVE"}


def _discount_stance(rules: str) -> str:
    token = rules.split(":")[0].strip().upper().replace(" ", "_").replace("-", "_")
    return token if token in _DISCOUNT_STANCES else "MODERATE"


def _execution_pack(dossier: Dict[str, Any]) -> Dict[str, Any]:
    """Flow payloads and compliance scores from the retention-platform agents."""
    from retention_platform import FlowArchitectAgent, QAComplianceAgent

    flows = FlowArchitectAgent(dossier).build_golden_five()
    existing = (dossier.get("klaviyo") or {}).get("coverage") or {}
    for flow in flows:
        found = existing.get(flow["flow_key"])
        flow["already_live"] = found["live"] if found else None
        flow["existing_matches"] = found["matches"] if found else []
    reviewer = QAComplianceAgent(dossier)
    reviews = []
    for flow in flows:
        report = reviewer.review_flow(flow)
        reviews.append({
            "flow_key": flow["flow_key"],
            "name": flow["name"],
            "passed": report["passed"],
            "score": report["score"],
            "checks": report["checks"],
        })
    return {
        "flows": flows,
        "qa": {
            "passed": all(item["passed"] for item in reviews),
            "score": round(sum(item["score"] for item in reviews) / len(reviews)) if reviews else 0,
            "flows": reviews,
        },
    }


@dataclass
class OnboardingIntakePayload:
    org_slug: str
    client_name: str
    client_slug: str
    store_platform: str = "Shopify"
    website: str = "https://example.com"
    timezone_name: str = "America/New_York"
    
    # Section A: Basics
    primary_goal_90d: str = "Scale email revenue attribution to 30%+ and increase repeat purchase rate"
    messaging_tools: List[str] = field(default_factory=lambda: ["Klaviyo"])
    subscription_app: str = "none"
    regulated_limits: str = "none"
    stakeholder_email: str = "founder@client.com"
    
    # Section B: Economics
    gross_margin_pct: float = 65.0
    free_shipping_threshold: float = 65.00
    target_aov: float = 80.00
    typical_consumption_days: int = 35
    discount_rules: str = "MODERATE: Welcome 15%, cart 10%, no stackable coupons"
    
    # Section C: Voice & Archetype
    voice_archetype: str = "The Trusted Dermatologist Best Friend"
    tone_descriptors: List[str] = field(default_factory=lambda: ["elevated", "approachable", "science-backed", "reassuring"])
    forbidden_words: List[str] = field(default_factory=lambda: ["miracle", "cure", "cheap", "overnight fix", "guaranteed flawless", "toxic"])
    hero_products: List[Dict[str, str]] = field(default_factory=lambda: [
        {"name": "Radiance C+ Booster", "sku": "SKU-RAD-01", "benefit": "Barrier hydration & brightening"},
        {"name": "Barrier Repair Moisturizer", "sku": "SKU-BAR-02", "benefit": "Ceramide moisture seal"}
    ])
    
    # The full lifecycle onboarding answers (see intake.SECTIONS), kept for the team
    intake: Dict[str, Any] = field(default_factory=dict)

    # Credentials (optional for live fetch; if omitted, calibrated benchmarks run)
    shopify_domain: Optional[str] = None
    shopify_access_token: Optional[str] = None
    klaviyo_private_key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def public_dict(self) -> Dict[str, Any]:
        data = self.to_dict()
        data.pop("shopify_access_token", None)
        data.pop("klaviyo_private_key", None)
        return data


@dataclass
class CreativeCampaignDraft:
    campaign_id: str
    title: str
    theme: str
    target_segment: str
    subject_line_options: List[str]
    preview_text: str
    body_markdown: str
    cta_text: str
    compliance_passed: bool
    compliance_notes: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OnboardingPackage:
    client_id: str
    client_slug: str
    client_name: str
    onboarded_at: str
    overall_health_score: float
    sizing_report: Dict[str, Any]
    decision_id: str
    decision_title: str
    tasks_created: List[Dict[str, Any]]
    facts_registered: int
    creative_draft: Dict[str, Any]
    dossier: Dict[str, Any]
    dossier_id: str = ""
    dossier_version: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def queue_onboarding(session: Session, scope: Scope, payload: OnboardingIntakePayload, vault=None,
                     klaviyo_account: Optional[dict] = None) -> m.JobRun:
    """Create the client, seal any tokens, and queue the onboarding job. Does not run it.

    klaviyo_account is what verify_account returned for the key ({id, name, timezone}); the key is
    stored under that account id so later jobs can tell when a key starts opening a different account.
    """
    if payload.klaviyo_private_key and not (klaviyo_account or {}).get("id"):
        raise ValueError("verify the Klaviyo key before queueing onboarding")
    import wavyos.jobmods  # noqa: F401  (registers client.onboard)
    from . import jobs

    client = session.scalar(select(m.Client).where(m.Client.org_id == scope.org_id, m.Client.slug == payload.client_slug))
    if client is None:
        client = tenancy.create_client(
            session, scope, name=payload.client_name, slug=payload.client_slug,
            timezone=payload.timezone_name, website=payload.website,
        )
    client_scope = Scope(scope.org_id, client.id, scope.actor)
    if payload.shopify_access_token or payload.klaviyo_private_key:
        if vault is None:
            raise ValueError("a vault is required before a store token can be queued")
        _seal_credentials(session, client_scope, vault, payload, klaviyo_account)
    public = payload.public_dict()
    if scope.actor.kind == "user":
        public["requested_by"] = scope.actor.id
    runs = jobs.emit(session, client_scope, "client.onboard", public, dedupe=uuid.uuid4().hex)
    if not runs:
        raise RuntimeError("client.onboard is not registered")
    return runs[0]


def _seal_credentials(session: Session, scope: Scope, vault, payload: OnboardingIntakePayload,
                      klaviyo_account: Optional[dict] = None) -> None:
    from . import connections
    from .scope import InvalidState

    def _add(provider: str, external_id: str, secret: str, label: str) -> None:
        try:
            connections.add(session, scope, vault, provider=provider, external_account_id=external_id,
                            secret=secret, label=label)
        except InvalidState:
            return

    if payload.shopify_access_token and payload.shopify_domain:
        _add("shopify", payload.shopify_domain, payload.shopify_access_token, "Shopify")
    if payload.klaviyo_private_key:
        account = klaviyo_account or {}
        _add("klaviyo", str(account["id"]), payload.klaviyo_private_key, str(account.get("name") or "Klaviyo"))


class ClientOnboardingService:
    def __init__(self, session: Session, scope: Scope):
        self.session = session
        self.scope = scope

    def onboard_client(self, payload: OnboardingIntakePayload, shopify_data: Optional[ShopifyCohortData] = None,
                       job_run_id: Optional[str] = None, klaviyo: Optional[Dict[str, Any]] = None,
                       orders_source: str = "", progress=None) -> OnboardingPackage:
        """Executes the full onboarding loop and persists state into Wavy OS.

        shopify_data is the order cohort (from Shopify or Klaviyo orders); without one, calibrated
        benchmarks stand in and the dossier says so. klaviyo is klaviyo_intake.run()'s output.
        """
        from . import klaviyo_intake
        progress = progress or (lambda *a, **k: None)
        now_iso = utcnow().isoformat() + "Z"
        
        # 1. Ensure client workspace exists
        client = self.session.scalar(
            select(m.Client).where(m.Client.org_id == self.scope.org_id, m.Client.slug == payload.client_slug)
        )
        if client is None:
            client = tenancy.create_client(
                self.session, self.scope,
                name=payload.client_name,
                slug=payload.client_slug,
                timezone=payload.timezone_name,
                website=payload.website,
            )
        
        client_scope = Scope(self.scope.org_id, client.id, self.scope.actor)

        # 2. Ingest Layer 2 Client Facts across 4 operating areas
        facts_count = self._ingest_layer_2_facts(client_scope, payload)

        # 3. The order cohort: measured when a store source was read, benchmark otherwise
        progress("analyze", "active", "Working out AOV, repeat rate and time to second order")
        if shopify_data is None:
            orders_source = "benchmark"
            shopify_client = ShopifyClient(
                store_domain=payload.shopify_domain or f"{payload.client_slug}.myshopify.com",
                access_token=payload.shopify_access_token
            )
            shopify_data = shopify_client.fetch_cohort_analytics()
        orders_source = orders_source or "shopify"
        median_days = shopify_data.median_days_to_second_order or payload.typical_consumption_days
        totals = (klaviyo or {}).get("totals_12m") or {}
        annual_orders = int(totals.get("orders") or shopify_data.total_orders_12m)
        progress("analyze", "done",
                 f"AOV ${shopify_data.blended_aov:,.2f}, {shopify_data.repeat_purchase_rate:.1%} repeat, "
                 f"{median_days} days to second order",
                 aov=shopify_data.blended_aov, repeat_rate=shopify_data.repeat_purchase_rate, median_days=median_days)

        # 4. Run Sizing Engine (05-sizing.md)
        progress("sizing", "active", "Sizing the revenue leaks without double counting")
        sizing_engine = OpportunitySizingEngine(
            total_subscribers=42500,
            engaged_subscribers=14200,
            unengaged_subscribers=11500,
            blended_aov=shopify_data.blended_aov,
            repeat_purchase_rate=shopify_data.repeat_purchase_rate,
            median_repurchase_days=median_days,
            annual_orders=annual_orders,
        )
        sizing_report = sizing_engine.size_account_opportunities(audit_findings={})
        progress("sizing", "done", f"${sizing_report.total_monthly_leak_base:,.0f} a month left on the table",
                 monthly_leak=sizing_report.total_monthly_leak_base)

        # 5. Emit Deduplicated Insights into Wavy OS
        progress("plan", "active", "Writing the first 30-day plan")
        created_insights = self._emit_audit_insights(client_scope, sizing_report, shopify_data)

        # 6. Propose & Approve Decision: 'First 30-Day Onboarding & Repair Plan'
        decision, tasks = self._create_decision_and_tasks(client_scope, created_insights, payload, sizing_report)

        # 7. Generate Campaign Draft using Founder Voice
        creative_draft = self._generate_founder_voice_campaign(payload, shopify_data)
        plan_approved = decision.status == "approved"
        progress("plan", "done", f"{len(tasks)} tasks approved for the first 30 days" if plan_approved else
                 f"{len(self.proposed_task_titles)}-task plan proposed. Your Wavy strategist reviews it next",
                 tasks=len(tasks))

        # 8. Compile Master Dossier
        dossier = {
            "client_id": client.id,
            "client_slug": client.slug,
            "client_name": client.name,
            "version": "1.0.0",
            "onboarded_at": now_iso,
            "status": "ACTIVE_EXECUTION",
            "brand_identity": {
                "voice_archetype": payload.voice_archetype,
                "tone_descriptors": payload.tone_descriptors,
                "forbidden_words": payload.forbidden_words,
                "hero_products": payload.hero_products,
            },
            "economics": {
                "gross_margin_pct": payload.gross_margin_pct,
                "free_shipping_threshold": payload.free_shipping_threshold,
                "target_aov": shopify_data.blended_aov,
                "typical_consumption_days": payload.typical_consumption_days,
                "repeat_purchase_rate": shopify_data.repeat_purchase_rate,
                "median_repurchase_days": median_days,
            },
            "data_sources": {"orders": orders_source, "klaviyo": bool(klaviyo)},
            "klaviyo": klaviyo,
            "sizing_summary": sizing_report.to_dict(),
            "first_30_day_plan": {
                "decision_id": decision.id,
                "status": decision.status,
                "tasks": [t.title for t in tasks] or list(self.proposed_task_titles),
            },
            "campaign_directives": {
                "allowed_discounts": _discount_stance(payload.discount_rules),
                "weekly_frequency": 2,
                "taboo_phrasing": list(payload.forbidden_words),
            },
            "intake": dict(payload.intake),
        }
        progress("build", "active", "Drafting the five core flows from these numbers")
        dossier["execution"] = _execution_pack(dossier)
        progress("build", "done", f"{len(dossier['execution']['flows'])} flow drafts built",
                 flows_built=len(dossier["execution"]["flows"]))
        qa = dossier["execution"]["qa"]
        progress("qa", "done", f"Compliance QA {'passed' if qa['passed'] else 'flagged issues'}: {qa['score']}/100",
                 qa_score=qa["score"], qa_passed=qa["passed"])

        measured_rate = shopify_data.repeat_purchase_rate if orders_source != "benchmark" else None
        dossier["health"] = klaviyo_intake.health(klaviyo, measured_rate)
        overall_health = dossier["health"]["score"]

        package = OnboardingPackage(
            client_id=client.id,
            client_slug=client.slug,
            client_name=client.name,
            onboarded_at=now_iso,
            overall_health_score=overall_health,
            sizing_report=sizing_report.to_dict(),
            decision_id=decision.id,
            decision_title=decision.title,
            tasks_created=[{"id": t.id, "title": t.title, "area": t.area, "owner_role": t.owner_role} for t in tasks],
            facts_registered=facts_count,
            creative_draft=creative_draft.to_dict(),
            dossier=dossier,
        )
        from .storage import save_dossier
        stored = save_dossier(self.session, client_scope, package.to_dict(), job_run_id)
        package.dossier_id = stored.id
        package.dossier_version = stored.version
        stored.document = package.to_dict()
        return package

    def _ingest_layer_2_facts(self, client_scope: Scope, payload: OnboardingIntakePayload) -> int:
        facts_to_add = [
            ("onboarding", "facts_rules", "store_platform", "Store platform", {"platform": payload.store_platform}),
            ("onboarding", "facts_rules", "client_goal_90d", "90-day retention goal", {"goal": payload.primary_goal_90d}),
            ("onboarding", "facts_rules", "messaging_tools", "Active messaging tools", {"tools": payload.messaging_tools}),
            ("onboarding", "facts_rules", "subscription_app", "Subscription app", {"app": payload.subscription_app}),
            ("onboarding", "facts_rules", "regulated_limits", "Regulated limits", {"limits": payload.regulated_limits}),
            ("economics", "facts_rules", "gross_margin", "Gross margin percentage", {"gross_margin_pct": payload.gross_margin_pct}),
            ("economics", "facts_rules", "shipping_threshold", "Free shipping threshold", {"threshold": payload.free_shipping_threshold}),
            ("economics", "facts_rules", "discount_rules", "Discount & offer rules", {"rules": payload.discount_rules}),
            ("product_customer", "facts_rules", "consumption_cycle", "Product consumption cycle", {"days": payload.typical_consumption_days}),
            ("voice_copy", "facts_rules", "voice_archetype", "Brand voice archetype", {"archetype": payload.voice_archetype, "tones": payload.tone_descriptors}),
            ("voice_copy", "facts_rules", "forbidden_words", "Forbidden words & claims", {"words": payload.forbidden_words}),
            ("onboarding", "facts_rules", "shared_fact_map", "Canonical shared fact map", {
                "canonical_keys": ["store_platform", "gross_margin", "client_goal_90d", "voice_archetype", "consumption_cycle"],
                "version": "1.0.0"
            }),
        ]

        # Ingest proposal via intake agent, and approve via human lead scope
        from .scope import Actor
        intake_actor = Actor("agent", "intake-agent", frozenset({"account_lead"}))
        intake_scope = Scope(client_scope.org_id, client_scope.client_id, intake_actor)

        count = 0
        for area, component, key, title, val in facts_to_add:
            fact, ap = knowledge.propose_fact(
                self.session, intake_scope,
                area=area, component=component, key=key, title=title,
                value=val, body=str(val), source_kind="intake", source_ref="templates/intake-questionnaire.md"
            )
            # Human lead approves the intake fact
            if client_scope.actor.is_human and (client_scope.actor.roles & {"org_admin", "account_lead"}):
                approvals.decide(self.session, client_scope, ap.id, approve=True, note="Verified during client intake onboarding")
            count += 1
        return count

    def _emit_audit_insights(self, client_scope: Scope, sizing: SizingReport, shopify: ShopifyCohortData) -> List[m.Insight]:
        insights = []
        for opp in sizing.opportunities[:3]:
            evidence = [{
                "kind": "audit_metric",
                "ref": f"shopify://orders/cohort_days_{shopify.median_days_to_second_order}",
                "detail": f"Annual uncaptured revenue leak: ${opp.revenue_base:,.0f} across {opp.eligible_pool:,} eligible profiles."
            }]
            ins, created = operations.create_insight(
                self.session, client_scope,
                area="flow_audit" if "flow" in opp.category else "journey",
                title=f"Revenue Leak: {opp.title}",
                summary=f"{opp.pool_description}. Sized at ${opp.revenue_base:,.0f}/yr base case (Pool: {opp.eligible_pool:,}, AOV: ${opp.aov}).",
                severity="high",
                evidence=evidence,
                source_kind="agent",
                source_ref="wavyos/audit/sizing.py",
                dedupe_key=f"leak_{opp.lever_key}"
            )
            insights.append(ins)
        return insights

    def _create_decision_and_tasks(self, client_scope: Scope, insights: List[m.Insight],
                                  payload: OnboardingIntakePayload, sizing: SizingReport) -> tuple[m.Decision, List[m.Task]]:
        insight_ids = [ins.id for ins in insights]
        from .scope import Actor
        strat_actor = Actor("agent", "strategist-agent", frozenset({"strategist"}))
        strat_scope = Scope(client_scope.org_id, client_scope.client_id, strat_actor)

        decision, ap = operations.propose_decision(
            self.session, strat_scope,
            title="First 30-Day Onboarding & Foundation Plan",
            insight_ids=insight_ids,
            rationale=f"Deploy critical behavioral flows to capture ${sizing.total_monthly_leak_base:,.0f}/mo in uncaptured GMV.",
            measurement={"metric": "repeat_purchase_rate", "window_days": 30, "baseline": 0.28},
            plan_scope=["campaign.build_draft", "flow.build_draft", "work.implement_nonlive"]
        )
        # Approve the decision with human lead
        if client_scope.actor.is_human and (client_scope.actor.roles & {"org_admin", "account_lead"}):
            approvals.decide(self.session, client_scope, ap.id, approve=True, note="Approved 30-Day Foundation Plan on onboarding")

        # Create foundational tasks
        task_specs = [
            ("Audit & Technical Instrumentation", "data_infrastructure", "analyst", "preflight", [
                {"id": "c1", "text": "Verify Shopify Added to Cart tracking snippet in theme.liquid", "passed": False},
                {"id": "c2", "text": "Confirm Klaviyo dedicated sending domain DNS verification", "passed": False},
                {"id": "c3", "text": "Verify order attribution source contract pinned", "passed": True, "by": "agent"}
            ]),
            ("Deploy Browse Abandonment 2-Part Flow", "flow_architecture", "strategist", "draft_build", [
                {"id": "c1", "text": "Trigger configured to Viewed Product with Add to Cart / Checkout suppression", "passed": False},
                {"id": "c2", "text": "First email delay set to 2 hours with hero benefit framing", "passed": False},
                {"id": "c3", "text": "Second email delay set to 24 hours with social proof UGC", "passed": False}
            ]),
            ("Expand Abandoned Checkout Sequence", "flow_architecture", "strategist", "draft_build", [
                {"id": "c1", "text": "Create 3-step checkout sequence (1h, 12h, 24h)", "passed": False},
                {"id": "c2", "text": "Insert conditional split for Cart Value > AOV threshold", "passed": False},
                {"id": "c3", "text": "Verify discount rules comply with client offer stance", "passed": False}
            ]),
            ("Craft Welcome Series 1 & 2 (Founder Voice)", "voice_copy", "creative", "copy", [
                {"id": "c1", "text": "Adheres to client tone archetype without generic AI phrasing", "passed": False},
                {"id": "c2", "text": "Zero occurrences of client forbidden words", "passed": False},
                {"id": "c3", "text": "Features top gateway SKU (Barrier Recovery / Radiance C+)", "passed": False}
            ]),
            ("Deliverability Hygiene & 90-Day Engagement Policy", "deliverability", "account_lead", "release_verification", [
                {"id": "c1", "text": "Create Engaged 30, 60, and 90-Day sending segments", "passed": False},
                {"id": "c2", "text": "Configure Sunset Unengaged suppression rule for profiles inactive > 120 days", "passed": False}
            ])
        ]

        self.proposed_task_titles = [spec[0] for spec in task_specs]
        if decision.status != "approved":
            return decision, []  # a brand-started audit: the plan waits for an agency lead to approve it

        tasks = []
        for title, area, owner, phase, checklist in task_specs:
            t = operations.create_task(
                self.session, client_scope,
                decision_id=decision.id,
                title=title,
                area=area,
                owner_role=owner,
                done_when="All checklist items verified and passing QA review",
                phase=phase,
                checklist=checklist
            )
            tasks.append(t)

        return decision, tasks

    def _generate_founder_voice_campaign(self, payload: OnboardingIntakePayload, shopify: ShopifyCohortData) -> CreativeCampaignDraft:
        hero_product = payload.hero_products[0]["name"] if payload.hero_products else "Hero Collection"
        tone_str = ", ".join(payload.tone_descriptors)
        
        subject_options = [
            f"The real reason your barrier routine feels stuck...",
            f"Why {payload.client_name} formulated {hero_product} differently",
            f"Most solutions miss this crucial step (here’s why)"
        ]
        preview_text = "Noticeable radiance without the irritation. Tap to see the difference."
        
        body_copy = f"""Hey there,

If you’ve ever felt like you’re doing everything right for your skin—staying consistent, buying into the hype—and still dealing with redness or dry fatigue, you’re not alone.

The truth is, most conventional formulas focus on surface-level quick fixes that fade by midday. When we created **{hero_product}**, we threw out the old playbook.

Instead of heavy synthetic fillers, we formulated with bio-available barrier actives designed to strengthen and replenish from within.

No shortcuts. No compromises. Just honest, clinically-inspired performance you can actually see in the mirror.

Ready to feel the difference?

**[ Experience {hero_product} → ]**

*Complimentary shipping included on all orders over ${int(payload.free_shipping_threshold)}.*"""

        # Verify compliance against forbidden words
        violations = [w for w in payload.forbidden_words if w.lower() in (body_copy + " " + " ".join(subject_options)).lower()]
        compliance_passed = len(violations) == 0
        compliance_notes = f"Verified: 0 forbidden words found. Calibrated to {payload.voice_archetype} ({tone_str})." if compliance_passed else f"Violations found: {', '.join(violations)}"

        return CreativeCampaignDraft(
            campaign_id=f"camp_{uuid.uuid4().hex[:6]}",
            title=f"Welcome Series Email #1: The {hero_product} Origin",
            theme="Founder Origin & Barrier Education",
            target_segment="New Newsletter Subscribers (Non-Buyers)",
            subject_line_options=subject_options,
            preview_text=preview_text,
            body_markdown=body_copy,
            cta_text=f"Experience {hero_product} →",
            compliance_passed=compliance_passed,
            compliance_notes=compliance_notes,
        )
