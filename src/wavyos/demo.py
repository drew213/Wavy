"""A fictional demo workspace, so the console shows the whole process without real client data or model spend.

`wavyos demo` creates organization `wavy-demo` with one fictional brand, approved knowledge, an
audit-style insight, an approved decision and three tasks, then runs them through scripted
executors (named `fake`, and shown that way in the console). One task passes QA first time, one is
sent back once by QA, and one stops to ask a person to approve a send.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from . import approvals, knowledge, operations, tenancy
from . import models as m
from .execution import engine
from .execution.adapters import FakeAdapter
from .scope import scope_for_agent, scope_for_user

ORG = "wavy-demo"
CLIENT = "northwind-demo"


def _produce(packet):
    title = packet["task"]["title"]
    fb = packet.get("previous_qa_feedback")
    if "approval" in title.lower():
        if packet.get("approval_granted"):
            return [("save_output", {"kind": "report", "label": "Send log (demo)",
                                     "content": "DEMO: approval recorded; in production this step would schedule the send."}),
                    ("finish", {"summary": "recorded the approved send (demo)"})]
        return [("request_approval", {"action": "campaign.publish", "summary": "Send welcome email 1 to new subscribers (demo)",
                                      "payload": "Welcome email 1, revision 1, audience: new subscribers since Oct 1"}),
                ("finish", {"summary": "asked for approval to send"})]
    body = ("DEMO COPY\nSubject: Welcome to Northwind\nPreview: Your 10% welcome offer is inside\n\n"
            "Thanks for joining. Here's 10% off your first order, as promised on the signup form.")
    if fb:
        body += "\n\n(Revised after QA: removed the 'detox' wording.)"
    elif "email 2" in title.lower():
        body += "\n\nOur tea will detox your body."  # QA should catch this claim
    return [("get_context", {"area": "voice_copy"}),
            ("save_output", {"kind": "copy", "label": title + " (demo)", "content": body}),
            ("finish", {"summary": "drafted " + title})]


def _qa(packet):
    work = " ".join(a["content"] for a in packet["work_to_review"])
    calls = []
    for item in packet["task"]["checklist"]:
        ok = not ("claim" in item["text"] and "detox" in work.split("(Revised")[0] and "Revised" not in work)
        calls.append(("record_check", {"item_id": item["id"], "passed": ok,
                                       "note": "meets it" if ok else "says 'detox': a health claim the brand can't make"}))
    return calls + [("finish", {"summary": "reviewed against the checklist"})]


def build(factory) -> str:
    """Create (or reuse) the demo org and run its work. Returns a one-time API token for the demo admin."""
    with factory() as s:
        org = s.scalar(select(m.Organization).where(m.Organization.slug == ORG))
        if org is None:
            org, admin = tenancy.bootstrap_org(s, name="Wavy Demo", slug=ORG, admin_email="admin@demo.wavy.test", admin_name="Demo Admin")
            ad = scope_for_user(s, admin, None)
            c = tenancy.create_client(s, ad, name="Northwind Botanicals (demo)", slug=CLIENT, timezone="America/Phoenix")
            c.settings = {"allowed_providers": ["fake"], "daily_budget_usd": 5}
            owner = tenancy.add_user(s, ad, email="owner@northwind.demo.test", name="Demo Brand Owner", kind="client")
            tenancy.grant(s, ad, user_id=owner.id, role="client_approver", client_id=c.id)
            agents = {a.role: a for a in s.scalars(select(m.Agent).where(m.Agent.org_id == org.id))}
            ag = lambda r: scope_for_agent(s, agents[r], c.id)
            own = scope_for_user(s, owner, c.id)
            for key, title, area, body in [
                ("welcome_offer", "Welcome offer is 10% off the first order", "offers", "Shown on the signup form. No stacking."),
                ("claims_rule", "No health or detox claims", "voice_copy", "Herbal teas: describe taste and ritual, never effects."),
            ]:
                _, ap = knowledge.propose_fact(s, ag("account_lead"), area=area, component="facts_rules", key=key, title=title,
                                               body=body, source_kind="intake", source_ref="demo intake form",
                                               review_by=date.today() + timedelta(days=90))
                approvals.decide(s, own, ap.id, True, "confirmed")
            ins, _ = operations.create_insight(s, ag("analyst"), area="flow_audit", title="Welcome series has been off since March",
                                               summary="New subscribers since March got no welcome email.", severity="critical",
                                               evidence=[{"kind": "check", "ref": "demo-scorecard#FLW-01", "detail": "welcome flow status: draft"}],
                                               source_kind="check", source_ref="demo-scorecard#FLW-01", dedupe_key="check:FLW-01")
            operations.create_insight(s, ag("analyst"), area="deliverability", title="Gmail spam rate near the 0.3% line",
                                      summary="Watch before scaling sends.", severity="high",
                                      evidence=[{"kind": "check", "ref": "demo-scorecard#DLV-01", "detail": "0.27% last 30 days (demo)"}],
                                      source_kind="check", source_ref="demo-scorecard#DLV-01")
            d, ap = operations.propose_decision(s, ag("strategist"), title="Rebuild the welcome series", insight_ids=[ins.id],
                                                rationale="It's the highest-value broken flow.", plan_scope=["flow.build_draft"],
                                                measurement={"metric": "welcome_revenue_per_recipient", "window_days": 30, "baseline": 0})
            approvals.decide(s, own, ap.id, True, "go ahead")
            for title in ("Welcome email 1", "Welcome email 2", "Send approval for welcome email 1"):
                t = operations.create_task(s, ag("strategist"), decision_id=d.id, title=title, area="voice_copy", owner_role="creative",
                                           done_when="copy saved and QA passed", due_date=date.today() + timedelta(days=5),
                                           checklist=["uses the approved 10% offer", "no health or detox claims"])
                engine.dispatch(s, ag("strategist"), t.id)
            s.commit()
        admin = s.scalar(select(m.User).where(m.User.org_id == org.id, m.User.email == "admin@demo.wavy.test"))
        token = tenancy.issue_token(s, admin, "demo console")
        s.commit()
    engine.work(factory, {"fake": FakeAdapter(lambda p: _qa(p) if p["purpose"] == "qa" else _produce(p))})
    return token
