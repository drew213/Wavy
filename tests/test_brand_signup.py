"""A brand signs itself up, connects Klaviyo, gets its audit, and can reopen it later; staff see it too."""
from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from wavyos import accounts, jobs
from wavyos import models as m

from test_klaviyo_onboarding import KEY, _services, env, fake_klaviyo  # noqa: F401


def _signup(c, email="maya@glowlab.co", brand="Glow Lab"):
    r = c.post("/v1/ops/signup", json={"brand_name": brand, "name": "Maya", "email": email,
                                      "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    return r.json()


def _as(client: TestClient, token: str) -> TestClient:
    c = TestClient(client.app)
    c.headers["Authorization"] = "Bearer " + token
    return c


def test_password_hash_round_trips_and_rejects_wrong_passwords():
    stored = accounts.hash_password("correct horse battery")
    assert stored.startswith("scrypt$") and "correct horse" not in stored
    assert accounts.check_password("correct horse battery", stored)
    assert not accounts.check_password("correct horse batterz", stored)
    assert not accounts.check_password("anything", "garbage")


def test_signup_creates_a_brand_workspace_and_sign_in_works(env):
    factory, staff, _, db_file = env
    anon = TestClient(staff.app)
    made = _signup(anon)
    assert made["client_slug"] == "glow-lab"

    brand = _as(staff, made["token"])
    me = brand.get("/v1/ops/me").json()
    assert me["kind"] == "client" and me["staff"] is False
    assert me["clients"] == [{"slug": "glow-lab", "name": "Glow Lab", "has_audit": False}]
    assert [c["slug"] for c in brand.get("/v1/ops/overview").json()["clients"]] == ["glow-lab"]

    assert anon.post("/v1/ops/login", json={"email": "MAYA@glowlab.co ", "password": "correct horse battery"}).status_code == 200
    wrong = anon.post("/v1/ops/login", json={"email": "maya@glowlab.co", "password": "wrong password!"})
    nobody = anon.post("/v1/ops/login", json={"email": "nobody@x.co", "password": "wrong password!"})
    assert wrong.status_code == nobody.status_code == 401 and wrong.json() == nobody.json()

    again = anon.post("/v1/ops/signup", json={"brand_name": "Glow Lab", "name": "M", "email": "maya@glowlab.co",
                                              "password": "another long one"})
    assert again.status_code == 409
    short = anon.post("/v1/ops/signup", json={"brand_name": "B", "name": "M", "email": "b@b.co", "password": "short"})
    assert short.status_code == 422
    assert not db_file.exists() or b"correct horse battery" not in db_file.read_bytes()  # no file on Postgres runs


def test_browser_sign_ins_last_30_days_and_sign_out_revokes_them(env):
    from datetime import timedelta

    from wavyos import tenancy
    from wavyos.db import utcnow
    factory, staff, _, _ = env
    anon = TestClient(staff.app)
    first = _signup(anon)["token"]
    second = anon.post("/v1/ops/login", json={"email": "maya@glowlab.co", "password": "correct horse battery"}).json()
    assert second["expires_in_days"] == 30
    with factory() as s:
        row = s.scalar(select(m.ApiToken).where(m.ApiToken.prefix == second["token"][:12]))
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=utcnow().tzinfo)
        assert timedelta(days=29) < expires - utcnow() <= timedelta(days=30)
        user = s.get(m.User, row.user_id)
        script = tenancy.issue_token(s, user, "reporting script")
        row.expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert _as(staff, second["token"]).get("/v1/ops/me").status_code == 401  # expired

    assert anon.post("/v1/ops/logout", headers={"Authorization": "Bearer " + script}).json() == {"signed_out": True}
    assert _as(staff, script).get("/v1/ops/me").status_code == 200  # sign-out never touches script tokens
    assert anon.post("/v1/ops/logout", headers={"Authorization": "Bearer " + first}).status_code == 200
    assert _as(staff, first).get("/v1/ops/me").status_code == 401

    a, b = (anon.post("/v1/ops/login", json={"email": "maya@glowlab.co", "password": "correct horse battery"}).json()["token"]
            for _ in range(2))
    assert _as(staff, a).post("/v1/ops/logout-everywhere").json() == {"signed_out": 3}  # a, b and the expired one
    assert _as(staff, b).get("/v1/ops/me").status_code == 401
    assert _as(staff, script).get("/v1/ops/me").status_code == 200
    assert anon.post("/v1/ops/logout").status_code == 200  # no token: nothing to do, still fine


def test_a_signed_up_brand_runs_its_own_audit_and_the_plan_waits_for_the_agency(env):
    factory, staff, vault, _ = env
    made = _signup(TestClient(staff.app))
    brand = _as(staff, made["token"])

    other = brand.post("/v1/ops/onboard", json={"client_name": "X", "client_slug": "someone-else",
                                                "klaviyo_private_key": KEY})
    assert other.status_code == 404

    queued = brand.post("/v1/ops/onboard", json={"client_name": "Glow Lab", "client_slug": made["client_slug"],
                                                 "klaviyo_private_key": KEY})
    assert queued.status_code == 200, queued.text
    jobs.run_pending(factory, _services(vault, httpx.MockTransport(fake_klaviyo())), limit=10)
    status = brand.get(f"/v1/ops/onboard/{queued.json()['job_run_id']}").json()
    assert status["status"] == "succeeded", status.get("error")
    plan = status["result"]["dossier"]["first_30_day_plan"]
    assert plan["status"] == "proposed" and len(plan["tasks"]) >= 3

    with factory() as s:
        client = s.scalar(select(m.Client).where(m.Client.slug == made["client_slug"]))
        assert s.scalar(select(func.count()).select_from(m.Task).where(m.Task.client_id == client.id)) == 0
        assert s.scalar(select(m.Connection).where(m.Connection.client_id == client.id)).provider == "klaviyo"

    saved = brand.get(f"/v1/ops/clients/{made['client_slug']}/audit").json()
    assert saved["version"] == 1 and saved["result"]["dossier"]["klaviyo"]["coverage"]
    assert [v["version"] for v in saved["versions"]] == [1]
    assert staff.get(f"/v1/ops/clients/{made['client_slug']}/audit").json()["version"] == 1
    assert brand.get("/v1/ops/me").json()["clients"][0]["has_audit"] is True

    rival = _as(staff, _signup(TestClient(staff.app), email="sam@rival.co", brand="Rival")["token"])
    assert rival.get(f"/v1/ops/clients/{made['client_slug']}/audit").status_code == 404


ANSWERS = {
    "what_you_sell": "Clean skincare for sensitive skin.", "goals_90d": "Grow repeat revenue; rebuild welcome",
    "customer": "Women 28-45 with reactive skin.", "hero_products": "Barrier Serum, Gentle Cleanser",
    "discount_stance": "Occasional promotions only", "deepest_discount": "15% max, never on bundles",
    "voice_words": "calm, expert, warm", "never_sound_like": "hype, clinical",
    "avoid_claims": "cure, miracle, FDA approved", "email_platform": "Klaviyo", "sms_platform": "Attentive",
    "primary_contact": "Maya", "final_approver": "Maya", "success": "Repeat rate up 5 points by day 90",
    "economics": "AOV around $72, gross margin 64%", "shipping_returns": "Free shipping over $60, 30-day returns",
    "consumable_days": "Serum lasts about 6 weeks", "programs": ["Subscriptions", "Bundles"], "program_apps": "Recharge",
    "submitted_by_name": "Maya Chen", "store_platform": "WooCommerce",
}


def test_intake_answers_map_onto_the_build():
    from wavyos import intake
    f = intake.payload_fields(intake.clean(ANSWERS))
    assert f["tone_descriptors"] == ["calm", "expert", "warm"]
    assert {"hype", "clinical", "cure", "miracle", "fda approved"} <= set(f["forbidden_words"])
    assert [h["name"] for h in f["hero_products"]] == ["Barrier Serum", "Gentle Cleanser"]
    assert f["discount_rules"].startswith("VALUE_ADD_ONLY: 15% max")
    assert f["free_shipping_threshold"] == 60 and f["gross_margin_pct"] == 64 and f["target_aov"] == 72
    assert f["typical_consumption_days"] == 42 and f["subscription_app"] == "Recharge"
    assert f["messaging_tools"] == ["Klaviyo", "Attentive"]
    assert f["store_platform"] == "WooCommerce"
    assert len(intake.QUESTIONS) == 52 and sum(q["required"] for q in intake.QUESTIONS.values()) == 14


def test_brand_questionnaire_autosaves_and_feeds_the_audit(env):
    factory, staff, vault, _ = env
    made = _signup(TestClient(staff.app))
    brand = _as(staff, made["token"])
    slug = made["client_slug"]

    blank = brand.get(f"/v1/ops/intake?client={slug}").json()
    assert blank["answered"] == 0 and blank["total"] == 52 and len(blank["sections"]) == 8
    partial = brand.put("/v1/ops/intake", json={"client_slug": slug, "answers": {"what_you_sell": "Skincare"}}).json()
    assert partial["answered"] == 1 and brand.get(f"/v1/ops/intake?client={slug}").json()["answers"] == {"what_you_sell": "Skincare"}
    assert brand.put("/v1/ops/intake", json={"client_slug": slug, "answers": {"nope": "x"}}).status_code == 422
    assert brand.put("/v1/ops/intake", json={"client_slug": slug, "answers": {"email_platform": "AOL"}}).status_code == 422
    assert brand.put("/v1/ops/intake", json={"client_slug": "someone-else", "client_name": "X", "answers": {}}).status_code == 404

    early = brand.post("/v1/ops/onboard", json={"client_name": "Glow Lab", "client_slug": slug, "klaviyo_private_key": KEY, "use_intake": True})
    assert early.status_code == 422 and "finish the onboarding questions" in early.text

    assert brand.put("/v1/ops/intake", json={"client_slug": slug, "answers": ANSWERS}).json()["missing"] == []
    queued = brand.post("/v1/ops/onboard", json={"client_name": "Glow Lab", "client_slug": slug, "klaviyo_private_key": KEY, "use_intake": True})
    assert queued.status_code == 200, queued.text
    jobs.run_pending(factory, _services(vault, httpx.MockTransport(fake_klaviyo())), limit=10)
    d = brand.get(f"/v1/ops/clients/{slug}/audit").json()["result"]["dossier"]
    assert d["intake"]["success"] == ANSWERS["success"]
    with factory() as s:
        context = s.get(m.AuditRun, d["klaviyo"]["full_audit"]["run_id"]).context
        assert context["store_platform"] == "WooCommerce" and context["sells_subscriptions"] is True
        assert context["avoid_claims"] == ANSWERS["avoid_claims"]
    assert d["brand_identity"]["tone_descriptors"] == ["calm", "expert", "warm"]
    assert d["campaign_directives"]["allowed_discounts"] == "VALUE_ADD_ONLY"
    assert d["economics"]["free_shipping_threshold"] == 60
    assert brand.get(f"/v1/ops/intake?client={slug}").json()["submitted_at"]


def test_the_workspace_bundle_has_every_section_with_an_honest_state(env):
    from wavyos.workspace import SECTION_STATES, SECTIONS
    factory, staff, vault, _ = env
    made = _signup(TestClient(staff.app))
    brand = _as(staff, made["token"])
    slug = made["client_slug"]
    assert brand.get(f"/v1/ops/clients/{slug}/workspace").status_code == 404
    assert brand.put("/v1/ops/intake", json={"client_slug": slug, "answers": ANSWERS}).status_code == 200
    queued = brand.post("/v1/ops/onboard", json={"client_name": "Glow Lab", "client_slug": slug, "klaviyo_private_key": KEY,
                                                 "use_intake": True})
    assert queued.status_code == 200, queued.text
    jobs.run_pending(factory, _services(vault, httpx.MockTransport(fake_klaviyo())), limit=10)

    b = brand.get(f"/v1/ops/clients/{slug}/workspace").json()
    assert [s["id"] for s in b["sections"]] == [s[0] for s in SECTIONS]
    assert all(s["state"] in SECTION_STATES for s in b["sections"])
    assert all(s["reason"] for s in b["sections"] if s["state"] in ("partial", "unavailable", "queued", "collecting"))
    by = {s["id"]: s for s in b["sections"]}
    assert by["answers"]["state"] == "ready" and by["answers"]["data"]["answers"]["success"] == ANSWERS["success"]
    assert by["sources"]["data"]["definitions"]
    assert b["run"]["status"] in ("ready", "partial"), b["run"]
    assert b["version"] == 1 and [v["version"] for v in b["versions"]] == [1]
    assert by["foundational"]["data"]["checks"] and b["facts"]
    assert by["opportunities"]["narrative_state"] == "awaiting_review"
    fact = next(iter(b["facts"].values()))
    assert {"label", "window", "source", "time_basis", "evidence_class"} <= set(fact)
    assert brand.get(f"/v1/ops/clients/{slug}/workspace?version=9").status_code == 404
    rival = _as(staff, _signup(TestClient(staff.app), email="sam@rival.co", brand="Rival")["token"])
    assert rival.get(f"/v1/ops/clients/{slug}/workspace").status_code == 404


def test_staff_can_start_a_questionnaire_for_a_new_client(env):
    _, staff, _, _ = env
    r = staff.put("/v1/ops/intake", json={"client_slug": "new-brand", "client_name": "New Brand", "answers": {"voice_words": "bold"}})
    assert r.status_code == 200 and r.json()["client_slug"] == "new-brand"
    assert staff.get("/v1/ops/intake?client=new-brand").json()["answers"] == {"voice_words": "bold"}


def test_brand_pages_are_served_with_the_console_headers(env):
    _, staff, _, _ = env
    anon = TestClient(staff.app)
    for path in ("/start", "/login", "/audit/glow-lab", "/onboard"):
        r = anon.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"], path
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "Wavy Client Hub" in anon.get("/audit/glow-lab").text
    assert anon.get("/assets/geist-latin.woff2").headers["content-type"] == "font/woff2"
    assert anon.get("/assets/wavy-logo-white.png").status_code == 200
    assert anon.get("/assets/..%2Fworkspace.html").status_code == 404
    assert anon.get("/assets/index.html").status_code == 404


def test_staff_audits_keep_every_version(env):
    factory, staff, vault, _ = env
    services = _services(vault, httpx.MockTransport(fake_klaviyo()))
    for _ in range(2):
        r = staff.post("/v1/ops/onboard", json={"client_name": "Glow Lab", "client_slug": "glow-k",
                                                "klaviyo_private_key": KEY})
        assert r.status_code == 200
        jobs.run_pending(factory, services, limit=10)
    latest = staff.get("/v1/ops/clients/glow-k/audit").json()
    assert latest["version"] == 2 and [v["version"] for v in latest["versions"]] == [2, 1]
    assert latest["result"]["dossier"]["first_30_day_plan"]["status"] == "approved"
    assert staff.get("/v1/ops/clients/glow-k/audit?version=1").json()["version"] == 1
    assert staff.get("/v1/ops/clients/glow-k/audit?version=9").status_code == 404
