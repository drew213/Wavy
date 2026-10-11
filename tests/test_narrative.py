"""Phase 4: drafts are written from the fact ledger, held to it, critiqued by a second provider, and published
only after a strategist approves them (synthetic data and scripted writers only)."""
from __future__ import annotations

import json
import re

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from wavyos import jobs
from wavyos import models as m
from wavyos.narrative import facts_check, packet
from wavyos.narrative.prompts import CRITERIA, SECTIONS, SYSTEM_CRITIC
from wavyos.narrative.writers import FakeWriter

from test_brand_signup import ANSWERS, _as, _signup
from test_klaviyo_onboarding import KEY, _services, env, fake_klaviyo  # noqa: F401

PACKET = re.compile(r"Packet:\n(\{.*?\n\})(?:\n|$)", re.S)
INVENTED = "$123,457"


# ---------------------------------------------------------------- the facts check on its own

LEDGER = {
    "performance.store_revenue_month": {"value": 10012.4, "unit": "USD"},
    "cohorts.repeat_90d": {"value": 0.0312, "unit": "ratio"},
    "program.empty_weeks_13w": {"value": 4, "unit": "weeks"},
    "subscriptions.failed_3m": {"value": None, "unit": "people"},
}


def _draft(**insight):
    base = {"finding": "Store revenue was $10.0k last month.", "evidence": "3.1% of buyers came back by day 90.",
            "action": "Build a 3-email post-purchase series with 15% off, sent within 48 hours, in week 2.",
            "owner": "Wavy Studios", "when": "Week 2", "facts": ["performance.store_revenue_month", "cohorts.repeat_90d"]}
    return {"headline": "Revenue holds; the second order is the leak.", "insights": [{**base, **insight}]}


def test_numbers_in_the_ledger_pass_at_the_precision_written():
    out = facts_check.check("performance", _draft(), LEDGER)
    assert out["ok"], out["problems"]
    assert out["numbers_checked"] == 2  # $10.0k and 3.1%; the window, week and plan counts aren't claims


def test_a_number_not_in_the_ledger_is_blocked_never_fixed():
    out = facts_check.check("performance", _draft(evidence=f"Flows lost {INVENTED} and 3.5% came back."), LEDGER)
    assert not out["ok"]
    assert {p["token"] for p in out["problems"] if p.get("token")} == {INVENTED, "3.5%"}
    assert out["draft"]["insights"][0]["evidence"].startswith("Flows lost $123,457")  # left as written
    assert not facts_check.check("performance", _draft(finding="Buyers wait 41 days for a second order."), LEDGER)["ok"]


def test_insights_without_an_action_are_cut_and_methodology_breaks_are_flagged():
    d = _draft()
    d["insights"].append({"finding": "Something is off.", "evidence": "4 empty weeks.", "action": ""})
    out = facts_check.check("performance", d, LEDGER)
    assert out["ok"] and len(out["draft"]["insights"]) == 1 and out["cut"][0]["reason"] == "cut: no action"
    bad = facts_check.check("performance", _draft(finding="Flows drove incremental revenue of $10.0k."), LEDGER)
    assert any("not incremental" in p["reason"] for p in bad["problems"])
    assert facts_check.check("performance", _draft(finding="Attributed, not incremental: $10.0k."), LEDGER)["ok"]
    promise = facts_check.check("performance", _draft(action="This will generate $10.0k by week 4."), LEDGER)
    assert any("promises" in p["reason"] for p in promise["problems"])
    claims = facts_check.check("performance", _draft(action="Say it cures dry skin, in week 1."), LEDGER, terms=["cures"])
    assert any(p.get("token") == "cures" for p in claims["problems"])


def test_cited_facts_and_cards_must_exist_and_unavailable_facts_are_called_out():
    out = facts_check.check("performance", _draft(facts=["nope.fact"]), LEDGER)
    assert any(p["token"] == "nope.fact" for p in out["problems"])
    out = facts_check.check("performance", _draft(facts=["subscriptions.failed_3m"]), LEDGER)
    assert out["ok"] and "unavailable" in out["warnings"][0]["reason"]
    opp = {**_draft(), "cards": [{"id": "opp-made-up", "observation": "x", "outcome": "y", "steps": [], "effort_days": 1,
                                  "readout": {"metric": "m", "days": 45}}]}
    assert not facts_check.check("opportunities", opp, LEDGER, card_ids={"opp-dat-01"})["ok"]
    assert not facts_check.check("journeys", _draft(), LEDGER)["ok"]  # missing ultimate_journey


def test_the_packet_carries_no_personal_data():
    dirty = {"rows": [{"email": "jane@example.com", "name": "Welcome", "note": "reply to jane.doe@shop.co or "
                       "support@shop.co, call +1 (555) 201-3344"}], "first_name": "Jane"}
    clean = packet.scrub(dirty)
    text = json.dumps(clean)
    assert "jane" not in text.lower() and "201-3344" not in text and "support@shop.co" in text
    assert clean["rows"][0]["name"] == "Welcome"


# ---------------------------------------------------------------- the loop through the job queue

def drafter(*, invent_first: bool = True):
    def reply(system: str, prompt: str) -> str:
        section = re.match(r"Section: (\w+)\.", prompt)[1]
        pk = json.loads(PACKET.search(prompt)[1])
        fid, v = next((k, f["value"]) for k, f in sorted(pk["facts"].items())
                      if isinstance(f["value"], (int, float)) and not isinstance(f["value"], bool) and "e" not in repr(f["value"]))
        insight = {"finding": f"The measured value is {v}.", "evidence": f"{v}, from the ledger.",
                   "action": "Rebuild the welcome flow's first message in week 1.", "owner": "Wavy Studios",
                   "when": "Week 1", "facts": [fid]}
        d = {"headline": f"One number to move first: {v}.", "insights": [insight]}
        if invent_first and "Your previous draft" not in prompt:
            d["headline"] += f" It will drive {INVENTED} in incremental revenue."
            d["insights"].append({"finding": "Something without an action.", "evidence": "none", "action": ""})
        if section == "opportunities":
            d["cards"] = [{"id": c["id"], "observation": f"Measured: {v}.", "outcome": "More second orders.",
                           "steps": ["Build a 3-email series"], "effort_days": 2,
                           "readout": {"metric": "repeat rate", "days": 45}} for c in pk["opportunities"][:2]]
        elif section == "journeys":
            d["ultimate_journey"] = [{"stage": "capture", "node": "Welcome", "trigger": "Signed up", "status": "fix",
                                      "detail": "Add a second message after 2 days"}]
        elif section == "calendar":
            d["calendar_90d"] = [{"week": 1, "theme": "Warm-up", "sends": [{"channel": "email", "segment": "Engaged 90 days",
                                                                           "idea": "Best sellers", "offer": "none"}]}]
        elif section == "report":
            d["summary"] = [f"The lead number this month is {v}."]
            d["next_month"] = [{"move": "Rebuild the welcome flow", "owner": "Wavy Studios", "kpi": "welcome revenue",
                                "baseline_fact": fid}]
        elif section == "plan":
            d["phases"] = [{"name": "Foundation", "weeks": "1-3", "goal": "Fix the leaks", "items": [
                {"week": 1, "ships": "Welcome rebuild", "owner": "Wavy Studios", "depends_on": "Brand approval",
                 "kpi": "welcome revenue per recipient", "baseline_fact": fid}]}]
        return json.dumps(d)
    return reply


def critic(*, harsh: bool = False):
    def reply(system: str, prompt: str) -> str:
        bad = harsh or "The automated facts check found:\nnone" not in prompt
        return json.dumps({"scores": {k: 0.5 if bad else 1 for k in CRITERIA},
                           "notes": ["Remove the number that isn't in the ledger and cut the empty insight."] if bad else []})
    return reply


def _brand_with_audit(env, *, providers, writers, settings=None):
    factory, staff, vault, _ = env
    made = _signup(TestClient(staff.app))
    brand = _as(staff, made["token"])
    slug = made["client_slug"]
    with factory() as s:
        c = s.scalar(select(m.Client).where(m.Client.slug == slug))
        c.settings = {**(c.settings or {}), "allowed_providers": providers, **(settings or {})}
        s.commit()
    assert brand.put("/v1/ops/intake", json={"client_slug": slug, "answers": ANSWERS}).status_code == 200
    assert brand.post("/v1/ops/onboard", json={"client_name": "Glow Lab", "client_slug": slug, "klaviyo_private_key": KEY,
                                               "use_intake": True}).status_code == 200
    done = jobs.run_pending(factory, {**_services(vault, httpx.MockTransport(fake_klaviyo())), "writers": writers},
                            limit=10)
    return brand, slug, {j.job_key: j for j in done}


def test_drafts_pass_the_check_and_critic_then_wait_for_a_strategist(env):
    factory, staff, _, _ = env
    first, second = FakeWriter(drafter(), cost_usd=0.01), FakeWriter(critic())
    brand, slug, done = _brand_with_audit(env, providers=["anthropic", "codex"],
                                          writers={"anthropic": first, "codex": second})
    assert done["narrative.draft"].status == "succeeded", done["narrative.draft"].error

    with factory() as s:
        modules = set(s.scalars(select(m.AuditFact.module)))
        assert {"checks"} <= modules  # the scorecard's numbers are in the ledger too
    queue = staff.get(f"/v1/ops/narrative?client={slug}").json()["drafts"]
    assert sorted(d["section"] for d in queue) == sorted(SECTIONS)
    assert all(d["status"] == "draft" and d["rounds"] == 2 and d["facts_ok"] and d["score"] == 10 and d["independent"]
               for d in queue), queue
    assert all(d["drafter"] == "anthropic" and d["critic_by"] == "codex" for d in queue)
    assert len(first.calls) == len(second.calls) == 2 * len(SECTIONS)
    assert all(INVENTED not in json.dumps(d) for d in queue)

    detail = staff.get(f"/v1/ops/narrative/{queue[0]['id']}").json()
    assert detail["evidence"] and all("value" in e for e in detail["evidence"])
    assert detail["critic"]["rounds"][0]["total"] == 5 and detail["critic"]["rounds"][0]["problems"] > 0
    assert detail["cost_usd"] == 0.02  # two drafter calls at $0.01 each; the critic's calls cost 0
    pending = [a for a in staff.get("/v1/ops/approvals").json()["approvals"] if a["action"] == "narrative.approve"]
    assert len(pending) == len(SECTIONS)

    # the brand sees none of it until a strategist approves
    assert brand.get("/v1/ops/narrative").status_code == 404
    assert brand.get(f"/v1/ops/narrative/{queue[0]['id']}").status_code == 404
    ws = brand.get(f"/v1/ops/clients/{slug}/workspace").json()
    assert all(s_["narrative"] is None for s_ in ws["sections"]) and ws["status"] == "measured"

    by = {d["section"]: d["id"] for d in queue}
    assert staff.post(f"/v1/ops/narrative/{by['opportunities']}/decide", json={"approve": True}).json()["status"] == "approved"
    ws = {s_["id"]: s_ for s_ in brand.get(f"/v1/ops/clients/{slug}/workspace").json()["sections"]}
    assert ws["opportunities"]["narrative_state"] == "approved" and ws["opportunities"]["narrative"]["headline"]
    assert ws["performance"]["narrative_state"] == "awaiting_review" and ws["performance"]["narrative"] is None

    perf = staff.get(f"/v1/ops/narrative/{by['performance']}").json()
    bad = {**perf["payload"], "headline": f"Flows lost {INVENTED}."}
    refused = staff.post(f"/v1/ops/narrative/{by['performance']}/edit", json={"payload": bad})
    assert refused.status_code == 422 and refused.json()["problems"][0]["token"] == INVENTED
    good = {**perf["payload"], "headline": "Revenue is steady; the second order is where it leaks."}
    assert staff.post(f"/v1/ops/narrative/{by['performance']}/edit",
                      json={"payload": good, "note": "tightened"}).json()["status"] == "edited"
    edited = staff.get(f"/v1/ops/narrative/{by['performance']}").json()
    assert edited["diff"] == [{"path": "headline", "before": perf["payload"]["headline"], "after": good["headline"]}]
    assert edited["original"]["headline"] == perf["payload"]["headline"]

    assert staff.post(f"/v1/ops/narrative/{by['cohorts']}/decide", json={"approve": False, "note": "too thin"}).json()["status"] == "rejected"
    ws = {s_["id"]: s_ for s_ in brand.get(f"/v1/ops/clients/{slug}/workspace").json()["sections"]}
    assert ws["performance"]["narrative"]["status"] == "edited" and ws["cohorts"]["narrative"] is None
    assert staff.post(f"/v1/ops/narrative/{by['cohorts']}/decide", json={"approve": True}).status_code == 409

    for sid in SECTIONS:
        if sid not in ("opportunities", "performance", "cohorts"):
            staff.post(f"/v1/ops/narrative/{by[sid]}/decide", json={"approve": True})
    redraft = staff.post(f"/v1/ops/clients/{slug}/narrative", json={})
    assert redraft.status_code == 200 and redraft.json()["job_run_ids"]
    jobs.run_pending(factory, {"writers": {"anthropic": first, "codex": second}})
    after = {d["section"]: d["status"] for d in staff.get(f"/v1/ops/narrative?client={slug}").json()["drafts"]}
    assert after["opportunities"] == "approved" and after["performance"] == "edited" and after["cohorts"] == "rejected"


def test_the_spend_cap_stops_drafting_and_says_so(env):
    factory, staff, _, _ = env
    writers = {"anthropic": FakeWriter(drafter(), cost_usd=1.0), "codex": FakeWriter(critic(), cost_usd=1.0)}
    _, slug, done = _brand_with_audit(env, providers=["anthropic", "codex"], writers=writers,
                                      settings={"audit_llm_cap_usd": 3.0})
    queue = staff.get(f"/v1/ops/narrative?client={slug}").json()["drafts"]
    status = {d["section"]: d for d in queue}
    first = next(iter(SECTIONS))
    assert status[first]["status"] == "needs_rewrite" and "spend cap" in status[first]["reason"]
    assert all(d["status"] == "skipped" and "$3.00" in d["reason"] for k, d in status.items() if k != first)
    assert sum(d["cost_usd"] for d in queue) <= 3.0
    assert "$2.00 spent" in done["narrative.draft"].summary


def test_a_draft_that_never_passes_needs_a_strategist_edit_and_one_provider_is_flagged(env):
    factory, staff, _, _ = env
    write, judge = drafter(invent_first=False), critic(harsh=True)
    one = FakeWriter(lambda system, prompt: judge(system, prompt) if system == SYSTEM_CRITIC else write(system, prompt))
    brand, slug, _ = _brand_with_audit(env, providers=["anthropic"], writers={"anthropic": one})
    queue = {d["section"]: d for d in staff.get(f"/v1/ops/narrative?client={slug}").json()["drafts"]}
    perf = queue["performance"]
    assert perf["status"] == "needs_rewrite" and perf["rounds"] == 3 and perf["independent"] is False
    assert "5/10" in perf["reason"] and perf["approval_id"] is None
    assert staff.post(f"/v1/ops/narrative/{perf['id']}/decide", json={"approve": True}).status_code == 409
    payload = staff.get(f"/v1/ops/narrative/{perf['id']}").json()["payload"]
    assert staff.post(f"/v1/ops/narrative/{perf['id']}/edit", json={"payload": payload}).json()["status"] == "edited"
    ws = {s_["id"]: s_ for s_ in brand.get(f"/v1/ops/clients/{slug}/workspace").json()["sections"]}
    assert ws["performance"]["narrative_state"] == "approved"


def test_no_allowed_provider_means_nothing_leaves_and_the_job_says_why(env):
    _, staff, _, _ = env
    writer = FakeWriter(drafter())
    _, slug, done = _brand_with_audit(env, providers=[], writers={"anthropic": writer})
    assert writer.calls == []
    assert done["narrative.draft"].status == "no_action" and "No model provider" in done["narrative.draft"].summary
    assert all(d["status"] == "skipped" for d in staff.get(f"/v1/ops/narrative?client={slug}").json()["drafts"])
