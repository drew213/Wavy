"""Tests for the Onboarding, Shopify Connector, and Sizing Engine."""
import httpx
import pytest
from wavyos.connectors.shopify import ShopifyClient, ReadOnlyViolation, scrub_secrets
from wavyos.audit.sizing import OpportunitySizingEngine
from wavyos.onboarding import ClientOnboardingService, OnboardingIntakePayload
from wavyos.scope import Scope, Actor
from wavyos import tenancy, models as m


def test_shopify_scrub_and_read_only():
    assert "shpat_***" in scrub_secrets("Invalid token shpat_1234567890abcdef")
    client = ShopifyClient("demo-store.myshopify.com")
    cohort = client.fetch_cohort_analytics()
    assert cohort.store_domain == "demo-store.myshopify.com"
    assert cohort.repeat_purchase_rate > 0.20
    assert len(cohort.top_gateway_skus) > 0


def test_sizing_engine_deduplication():
    engine = OpportunitySizingEngine(
        total_subscribers=50000,
        engaged_subscribers=15000,
        unengaged_subscribers=10000,
        blended_aov=85.00,
        repeat_purchase_rate=0.28,
        median_repurchase_days=35,
        annual_orders=30000
    )
    report = engine.size_account_opportunities(audit_findings={})
    assert report.total_annual_leak_base > 0
    assert report.total_monthly_leak_base > 0
    assert len(report.opportunities) >= 5
    # Verify opportunities are ranked by value-effort ratio
    ratios = [o.value_effort_ratio for o in report.opportunities]
    assert ratios == sorted(ratios, reverse=True)
    # Check dedupe summary
    assert "browse_visitors" in report.dedupe_summary["pools_claimed"]
    assert "cart_abandoners" in report.dedupe_summary["pools_claimed"]


def test_onboarding_service_lifecycle(session, world):
    scope = world.user(world.admin, None)
    service = ClientOnboardingService(session, scope)
    
    payload = OnboardingIntakePayload(
        org_slug="wavy",
        client_name="Glow Lab",
        client_slug="glow-lab",
        primary_goal_90d="Reach 32% email attribution",
        gross_margin_pct=70.0,
        free_shipping_threshold=60.0,
        target_aov=85.0,
        typical_consumption_days=30,
        voice_archetype="The Clinical Esthetician",
        tone_descriptors=["authoritative", "warm", "science-backed"],
        forbidden_words=["miracle", "cure", "cheap"],
        hero_products=[{"name": "Barrier Serum", "sku": "SER-01", "benefit": "Hydration"}]
    )
    
    pkg = service.onboard_client(payload)
    
    assert pkg.client_slug == "glow-lab"
    assert pkg.facts_registered >= 10
    assert len(pkg.tasks_created) == 5
    assert pkg.overall_health_score is None  # benchmark data only: nothing measured, so no score is invented
    assert pkg.dossier["data_sources"]["orders"] == "benchmark"
    assert pkg.creative_draft["compliance_passed"] is True
    assert len(pkg.creative_draft["subject_line_options"]) == 3
    
    # Verify database persistence
    from sqlalchemy import select
    client = session.scalar(select(m.Client).where(m.Client.org_id == scope.org_id, m.Client.slug == "glow-lab"))
    assert client is not None
    
    facts = session.query(m.ClientFact).filter_by(client_id=client.id).all()
    assert len(facts) >= 10
    
    insights = session.query(m.Insight).filter_by(client_id=client.id).all()
    assert len(insights) >= 3
    
    decisions = session.query(m.Decision).filter_by(client_id=client.id).all()
    assert len(decisions) >= 1
    assert decisions[0].title == "First 30-Day Onboarding & Foundation Plan"
    
    tasks = session.query(m.Task).filter_by(client_id=client.id).all()
    assert len(tasks) == 5

    execution = pkg.dossier["execution"]
    assert [flow["flow_key"] for flow in execution["flows"]] == [
        "browse_abandonment",
        "added_to_cart",
        "abandoned_checkout",
        "replenishment",
        "customer_winback",
    ]
    assert execution["qa"]["passed"] is True
    assert execution["qa"]["score"] == 100
    stored = session.query(m.Dossier).filter_by(client_id=client.id).one()
    assert stored.version == 1
    assert stored.document["dossier_id"] == stored.id
    assert stored.document["dossier"]["execution"]["qa"]["passed"] is True


def test_shopify_pages_resume_without_duplicating_orders(session, world):
    from wavyos.storage import cohort_from_orders, ingest_shopify

    scope = world.user(world.admin, world.a)

    def handler(request: httpx.Request) -> httpx.Response:
        if "page_info=p2" in str(request.url):
            return httpx.Response(200, json={"orders": [_order(2, "2026-03-02T00:00:00Z", "40.00")]})
        return httpx.Response(200, json={"orders": [_order(1, "2026-02-01T00:00:00Z", "80.00")]}, headers={
            "Link": '<https://brand-a.myshopify.com/admin/api/2024-04/orders.json?limit=250&page_info=p2>; rel="next"'
        })

    client = ShopifyClient(
        "brand-a.myshopify.com", "shpat_live_example",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    first = ingest_shopify(session, scope, client, max_pages=1)
    assert first.orders_stored == 1 and first.next_url and "page_info=p2" in first.next_url
    second = ingest_shopify(session, scope, client, max_pages=5)
    assert second.next_url is None and second.orders_stored == 2
    again = ingest_shopify(session, scope, client, max_pages=5)
    assert again.orders_stored == 2
    cohort = cohort_from_orders(session, scope, "brand-a.myshopify.com")
    assert cohort.total_orders_12m == 2
    assert cohort.repeat_customer_count == 1
    assert cohort.blended_aov == 60.0
    assert cohort.median_days_to_second_order == 29


def _order(order_id, created_at, total):
    return {
        "id": order_id, "total_price": total, "created_at": created_at, "currency": "USD",
        "customer": {"id": 7}, "line_items": [{"title": "Serum"}], "financial_status": "paid",
    }
