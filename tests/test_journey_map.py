"""The journey map: stored email HTML and previews, the flow tree with message suggestions, the optimal journey,
history between audits, the journeys packet and the preview endpoint (synthetic data only)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from wavyos import email_preview, tenancy
from wavyos import models as m
from wavyos.analysis import journey_map
from wavyos.analysis.base import Analysis
from wavyos.api import create_app
from wavyos.audit import optimal_journey
from wavyos.audit.snapshot import Snapshot
from wavyos.db import create_all, make_engine, make_sessionmaker
from wavyos.narrative import facts_check, packet
from wavyos.scope import scope_for_user


def _send(aid, mid, subject, nxt=None, status="live", preview="", template=None, smart=True):
    return {"id": aid, "type": "send-email", "links": {"next": nxt},
            "data": {"status": status, "message": {"id": mid, "name": f"Email {mid}", "subject_line": subject,
                                                   "preview_text": preview, "template_id": template,
                                                   "smart_sending_enabled": smart}}}


def _delay(aid, value, unit, nxt):
    return {"id": aid, "type": "time-delay", "links": {"next": nxt}, "data": {"value": value, "unit": unit}}


WELCOME = {"triggers": [{"type": "list", "id": "L1"}], "entry_action_id": "a1",
           "profile_filter": {"condition_groups": [{"conditions": [{"type": "profile-metric", "metric_id": "PO",
                                                                    "measurement_filter": {"operator": "equals", "value": 0},
                                                                    "timeframe_filter": {"operator": "flow-start"}}]}]},
           "actions": [_send("a1", "M1", "Welcome to the shop", "a2", preview="Your code is inside", template="T1"),
                       _delay("a2", 1, "days", "a3"),
                       {"id": "a3", "type": "conditional-split", "data": {"profile_filter": {"condition_groups": [{"conditions": [
                           {"type": "profile-metric", "metric_id": "PO", "measurement_filter": {"operator": "greater-than", "value": 0},
                            "timeframe_filter": {"operator": "alltime"}}]}]}},
                        "links": {"next_if_true": "a4", "next_if_false": "a5"}},
                       _send("a4", "M2", "Thanks for buying", status="draft"),
                       _send("a5", "M3", "Last chance for your welcome code, it ends at midnight tonight for sure", template="T2")]}
CHECKOUT = {"triggers": [{"type": "metric", "id": "SC"}], "entry_action_id": "b1",
            "actions": [_delay("b1", 6, "hours", "b2"),
                        _send("b2", "M4", "Here's 10% off your cart", "b3", preview="Finish your order", template="T3"),
                        _delay("b3", 1, "days", "b4"),
                        {"id": "b4", "type": "ab-test", "links": {"next": None},
                         "data": {"main_action": {"type": "send-email", "data": {"status": "live", "message": {
                             "id": "M5", "name": "Checkout 2", "subject_line": "Still thinking it over?", "preview_text": "We saved it"}}},
                                  "current_experiment": {"variations": [{"id": "v1"}, {"id": "v2"}]}}}]}
BROWSE = {"triggers": [{"type": "metric", "id": "VP"}], "entry_action_id": "c1",
          "actions": [_send("c1", "M6", "Still looking?", template="T3")]}


def _stats(rec, rpr, unsub=0, clicks=None):
    return {"recipients": rec, "delivered": rec, "conversion_value": rec * rpr, "unsubscribes": unsub,
            "spam_complaints": 0, "bounced": 0, "opens_unique": rec // 2,
            "clicks_unique": rec // 20 if clicks is None else clicks}


def _rows(per_message):
    return {"ok": True, "value": {"data": {"attributes": {"results": [
        {"groupings": {"flow_id": fid, "flow_name": fid, "flow_message_id": mid, "flow_message_name": mid,
                       "send_channel": "email"}, "statistics": st} for fid, mid, st in per_message]}}}}


def snapshot(**context) -> Snapshot:
    api = {"metrics": {"items": [{"id": "PO", "attributes": {"name": "Placed Order"}},
                                 {"id": "SC", "attributes": {"name": "Started Checkout"}},
                                 {"id": "VP", "attributes": {"name": "Viewed Product"}}]},
           "lists": {"items": [{"id": "L1", "attributes": {"name": "Newsletter"}}]},
           "flows": {"items": [{"id": "W1", "attributes": {"name": "Welcome Series", "status": "live"}},
                               {"id": "C1", "attributes": {"name": "Checkout Recovery", "status": "live"}},
                               {"id": "B1", "attributes": {"name": "Browse Abandonment", "status": "draft"}}],
                     "details": [{"flow": {"id": "W1", "attributes": {"name": "Welcome Series", "status": "live"}}, "definition": WELCOME},
                                 {"flow": {"id": "C1", "attributes": {"name": "Checkout Recovery", "status": "live"}}, "definition": CHECKOUT},
                                 {"flow": {"id": "B1", "attributes": {"name": "Browse Abandonment", "status": "draft"}}, "definition": BROWSE}]}}
    year = [("W1", "M1", _stats(1000, 2.0)), ("W1", "M3", _stats(1000, 0.2)),
            ("C1", "M4", _stats(1000, 3.0, unsub=30)), ("C1", "M5", _stats(1000, 1.5, clicks=2))]
    quarter = [("W1", "M1", _stats(300, 2.0)), ("W1", "M3", _stats(300, 0.2)),
               ("C1", "M4", _stats(300, 1.0)), ("C1", "M5", _stats(300, 1.5))]
    templates = {"templates": [
        {"templateId": "T1", "htmlHash": "h1", "htmlBytes": 120_000, "imageCount": 2, "imagesWithoutAlt": 2,
         "textChars": 900, "hasUnsubscribe": True, "textSample": "Welcome"},
        {"templateId": "T2", "htmlHash": "h2", "htmlBytes": 20_000, "imageCount": 3, "imagesWithoutAlt": 0,
         "textChars": 40, "hasUnsubscribe": False, "textSample": "Sale"},
        {"templateId": "T3", "htmlHash": "h3", "htmlBytes": 20_000, "imageCount": 1, "imagesWithoutAlt": 0,
         "textChars": 800, "hasUnsubscribe": True, "textSample": "Your cart"}]}
    return Snapshot(files={"api": api, "perf": {"reports": {"flow365": _rows(year), "flow90": _rows(quarter)}},
                           "templates": templates}, context=context)


def _run(**context):
    snap = snapshot(**context)
    a = Analysis(journey_map.MODULE)
    journey_map.run(snap, a, snap.context)
    return a


def _msgs(a):
    flows = {f["id"]: f for f in a.tables["flows"]}
    return {x["message_id"]: x for f in flows.values() for x in journey_map.walk_messages(f["tree"])}, flows


# ---------------------------------------------------------------- previews

def test_preview_resolves_template_tags_and_strips_active_content():
    html = ('<html><head><meta http-equiv="refresh" content="0;url=x"></head><body onload="x()">'
            "Hi {{ first_name|default:'there' }}{{ person.email }}{% if x %}!{% endif %}"
            '<script>alert(1)</script><a href="{% unsubscribe %}" onclick="y()">Unsubscribe</a></body></html>')
    out = email_preview.preview(html)
    assert "Hi there" in out and "{{" not in out and "{%" not in out
    assert "<script" not in out.lower() and "onclick" not in out and "onload" not in out and "refresh" not in out
    assert '<base target="_blank">' in out


def test_store_keeps_one_row_per_version_per_brand(session, world):
    h1 = email_preview.store(session, world.org.id, world.a.id, "T1", "<p>v1</p>")
    assert email_preview.store(session, world.org.id, world.a.id, "T1", "<p>v1</p>") == h1
    h2 = email_preview.store(session, world.org.id, world.a.id, "T1", "<p>v2</p>")
    email_preview.store(session, world.org.id, world.b.id, "T9", "<p>v1</p>")
    assert h1 != h2 and email_preview.store(session, world.org.id, world.a.id, "T1", "") is None
    assert session.query(m.EmailHtml).filter_by(client_id=world.a.id).count() == 2
    assert email_preview.get(session, world.b.id, h1).template_id == "T9"


# ---------------------------------------------------------------- the current journey

def test_flow_tree_has_every_wait_split_and_message():
    a = _run()
    msgs, flows = _msgs(a)
    tree = flows["W1"]["tree"]
    assert [x["kind"] for x in tree] == ["email", "delay", "split"]
    assert tree[1]["text"] == "Wait 1 day"
    assert tree[2]["text"] == "Has placed an order all time"
    assert [x["message_id"] for x in tree[2]["yes"]] == ["M2"] and [x["message_id"] for x in tree[2]["no"]] == ["M3"]
    assert flows["W1"]["exits_on_purchase"] and flows["W1"]["trigger"] == "Joins list: Newsletter"
    assert msgs["M1"]["html_hash"] == "h1" and msgs["M1"]["day"] == 0 and msgs["M1"]["p365"]["recipients"] == 1000
    assert msgs["M5"]["ab_test"] and msgs["M5"]["variants"] == 2 and msgs["M4"]["offer"] == "10% off"
    assert msgs["M4"]["day"] == 0.25 and not flows["B1"]["live"]
    assert {s["stage"]: s["status"] for s in a.tables["stages"]}["browse"] == "draft"


def test_each_live_message_gets_measured_suggestions():
    a = _run(regulated_category="No")
    msgs, _ = _msgs(a)
    rules = {mid: {s["rule"] for s in x["suggestions"]} for mid, x in msgs.items()}
    assert "switched_off" in rules["M2"]
    assert {"clipped", "missing_alt"} <= rules["M1"] and "no_preview" not in rules["M1"]
    assert {"weak_step", "image_only", "no_unsubscribe", "no_preview", "long_subject"} <= rules["M3"]
    assert {"discount_first", "slow_first_touch", "unsubscribes", "declining"} <= rules["M4"]
    assert "low_clicks" in rules["M5"]
    assert msgs["M1"].get("best_in_flow")
    assert not rules["M6"]  # the browse flow isn't live: its message isn't reviewed
    ids = {f.id for f in a.findings}
    assert {"journey_map.weak_steps", "journey_map.slow_first_touch", "journey_map.switched_off_steps",
            "journey_map.discount_first_touch"} <= ids
    weak = next(f for f in a.findings if f.id == "journey_map.weak_steps")
    assert weak.value["status"] == "sized" and weak.value["annual"]["base"] == 800.0  # (2.0 * 0.5 - 0.2) * 1000
    assert a.tables["messages"][0]["priority"] >= a.tables["messages"][-1]["priority"]


def test_journey_map_is_unavailable_without_flows():
    import pytest
    from wavyos.audit.snapshot import MissingData
    snap = Snapshot(files={"api": {"flows": {"items": [], "details": []}}})
    with pytest.raises(MissingData):
        journey_map.run(snap, Analysis(journey_map.MODULE), {})


# ---------------------------------------------------------------- the optimal journey

def _result(a):
    return {"modules": {journey_map.MODULE: {"status": "ok", "tables": a.tables}}, "facts": [],
            "findings": [f.as_dict() for f in a.findings]}


def test_optimal_journey_marks_moments_and_steps():
    a = _run()
    result = _result(a)
    cards = [{"id": "opp-flw-04", "check_id": "FLW-04", "sizing": {"status": "sized", "lever": "browse_abandonment",
                                                                   "annual": {"low": 1, "base": 5000, "high": 9}}}]
    optimal_journey.attach(result, cards, {"FLW-04": {"improved": 3, "flat": 0, "worse": 0, "unmeasured": 0}},
                           {"regulated_category": "CBD / hemp / THC"}, {"placed order", "started checkout", "viewed product"})
    moments = {x["id"]: x for x in result["modules"][journey_map.MODULE]["tables"]["optimal"]}
    assert moments["browse_abandon"]["status"] == "build" and moments["browse_abandon"]["drafts"][0]["name"] == "Browse Abandonment"
    assert moments["browse_abandon"]["value_base"] == 5000 and moments["browse_abandon"]["confidence"] > 1
    assert moments["browse_abandon"]["rank"] == 1
    checkout = moments["checkout_abandon"]
    assert checkout["status"] == "fix"
    first, sms, second = checkout["steps"][:3]
    assert first["status"] == "build" and first["message"] is None  # the live first email waits 6 hours
    assert sms["status"] == "validate" and "category" in sms["reason"]
    assert second["status"] == "fix" and second["message"]["message_id"] == "M5"
    welcome = moments["welcome_series"]["steps"]
    assert welcome[0]["message"]["message_id"] == "M1" and welcome[2]["message"]["message_id"] == "M3"
    assert moments["winback"]["status"] == "build"
    assert moments["cart_abandon"]["status"] == "validate"  # Added to Cart isn't tracked
    facts = {f["id"]: f["value"] for f in result["facts"]}
    assert facts["journey_map.moments_build"] >= 2 and facts["journey_map.moments_validate"] == 1


def test_history_marks_what_changed_since_the_previous_audit():
    before = _result(_run())
    after_a = _run()
    for r in after_a.tables["messages"]:
        if r["message_id"] == "M3":
            r["subject"] = "A new subject"
            r["suggestions"] = [s for s in r["suggestions"] if s["rule"] != "no_preview"]
    after = _result(after_a)
    journey_map.attach_history(after, before)
    row = next(r for r in after_a.tables["messages"] if r["message_id"] == "M3")
    assert row["since_last"]["subject_changed"] and row["since_last"]["resolved"] == ["no_preview"]
    assert row["since_last"]["rpr_365d_before"] == 0.2
    facts = {f["id"]: f["value"] for f in after["facts"]}
    assert facts["journey_map.messages_changed_since_last"] == 1 and facts["journey_map.suggestions_resolved_since_last"] == 1
    journey_map.attach_history(after, None)  # a first audit: nothing to compare, nothing raised


# ---------------------------------------------------------------- the written half

def test_journeys_packet_carries_the_map_and_past_edits():
    a = _run()
    result = _result(a)
    optimal_journey.attach(result, [], None, {}, {"placed order", "started checkout"})
    out = packet.journey(result["modules"], [{"draft": "2026-09-01", "edits": [{"path": "journey_copy[0].subject",
                                                                                 "before": "Old", "after": "New"}]}])
    assert out["optimal"] and all(x["status"] != "keep" for x in out["optimal"])
    assert out["messages"] and all(x["suggestions"] for x in out["messages"])
    assert out["learning"]["strategist_edits"][0]["edits"][0]["after"] == "New"
    draft = {"headline": "Checkout recovery is late and leads with a discount.",
             "insights": [{"finding": "f", "evidence": "e", "action": "Move the first email to an hour after entry.",
                           "owner": "Wavy Studios", "when": "Week 1", "facts": []}],
             "journey_copy": [{"moment": "checkout_abandon", "step": 1, "channel": "email", "subject": "You left something",
                               "preview": "It's still in your cart", "body": "Come back and finish.", "why": "remind"}],
             "message_rewrites": [{"message_id": "M4X9", "subject": "Your cart is waiting", "preview": "Saved for 24 hours",
                                   "change": "Lead with the product", "why": "discount_first"}]}
    assert facts_check.check("journeys", draft, {})["ok"]
    draft["journey_copy"][0]["body"] = "This cures everything."
    assert not facts_check.check("journeys", draft, {}, terms=["cures"])["ok"]


# ---------------------------------------------------------------- the endpoint and the page policy

def test_preview_endpoint_is_scoped_to_the_brand(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'j.db'}")
    create_all(eng)
    factory = make_sessionmaker(eng)
    with factory() as s:
        org, admin = tenancy.bootstrap_org(s, name="Agency", slug="agency", admin_email="admin@agency.test", admin_name="A")
        sc = scope_for_user(s, admin, None)
        a = tenancy.create_client(s, sc, name="Brand A", slug="brand-a", timezone="UTC")
        b = tenancy.create_client(s, sc, name="Brand B", slug="brand-b", timezone="UTC")
        owner = tenancy.add_user(s, sc, email="owner@a.test", name="Owner", kind="client")
        tenancy.grant(s, sc, user_id=owner.id, role="client_approver", client_id=a.id)
        h = email_preview.store(s, org.id, b.id, "T1", "<p>Hello {{ first_name|default:'friend' }}</p>")
        staff_token, brand_token = tenancy.issue_token(s, admin, "t"), tenancy.issue_token(s, owner, "t")
        s.commit()
    c = TestClient(create_app(factory=factory))
    ok = c.get(f"/v1/ops/clients/brand-b/emails/{h}", headers={"Authorization": "Bearer " + staff_token})
    assert ok.status_code == 200 and "Hello friend" in ok.json()["html"]
    assert c.get(f"/v1/ops/clients/brand-b/emails/{h}", headers={"Authorization": "Bearer " + brand_token}).status_code == 404
    assert c.get(f"/v1/ops/clients/brand-a/emails/{h}", headers={"Authorization": "Bearer " + brand_token}).status_code == 404
    assert c.get(f"/v1/ops/clients/brand-b/emails/{h}").status_code == 401
    page = c.get("/audit/brand-b").headers["content-security-policy"]
    assert "img-src 'self' data: https:" in page and "frame-ancestors 'none'" in page and "script-src 'self' 'unsafe-inline';" in page
    assert "img-src 'self' data:;" in c.get("/console").headers["content-security-policy"]
