"""The onboarding sequence: who gets which email when, once, paced and stoppable; how it's sent; brand approvals;
and the measured progress toward the first $10K."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from wavyos import impact
from wavyos import models as m
from wavyos.db import utcnow
from wavyos.mail import lifecycle, links, outbox, providers, render

from test_brand_signup import _as, _signup
from test_klaviyo_onboarding import _services, env, fake_klaviyo  # noqa: F401
from test_narrative import _brand_with_audit


def _settings(staff):
    return staff.app.state.services["settings"]


def _queued(factory, **where) -> list[m.OutboundEmail]:
    with factory() as s:
        q = select(m.OutboundEmail)
        for k, v in where.items():
            q = q.where(getattr(m.OutboundEmail, k) == v)
        rows = list(s.scalars(q.order_by(m.OutboundEmail.created_at)))
        s.expunge_all()
        return rows


def _plan(factory, staff, vault, now):
    with factory() as s:
        out = lifecycle.plan(s, _settings(staff), vault, now)
        s.commit()
        return out


def _keys(factory, to=None) -> list[str]:
    return [e.key.split(":")[0] for e in _queued(factory) if to is None or e.to_email == to]


# ---------------------------------------------------------------- sending

def test_quiet_hours_hold_nudges_until_morning_but_not_transactional_email():
    tz = "America/New_York"
    late = datetime(2026, 3, 3, 3, 30, tzinfo=timezone.utc)      # 22:30 in New York
    assert outbox.send_time(late, tz, "nudge") == datetime(2026, 3, 3, 13, 0, tzinfo=timezone.utc)
    assert outbox.send_time(late, tz, "transactional") == late
    early = datetime(2026, 3, 3, 10, 0, tzinfo=timezone.utc)     # 05:00
    assert outbox.send_time(early, tz, "nudge") == datetime(2026, 3, 3, 13, 0, tzinfo=timezone.utc)
    day = datetime(2026, 3, 3, 16, 0, tzinfo=timezone.utc)       # 11:00
    assert outbox.send_time(day, tz, "nudge") == day
    assert outbox.send_time(day, "Not/AZone", "nudge") == day   # unknown zone: UTC, 16:00 is daytime


def test_providers_send_with_their_own_auth_and_never_show_the_key():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.postmarkapp.com":
            return httpx.Response(200, json={"MessageID": "pm-1"})
        return httpx.Response(200, json={"id": "re-1"})

    msg = providers.Message(to="maya@glowlab.co", subject="Hi", html="<p>Hi</p>", text="Hi", sender="Wavy <hi@x.co>",
                            reply_to="team@x.co", headers={"List-Unsubscribe": "<https://x.co/u>"}, tag="welcome:1")
    pm = providers.make("postmark", "pm-secret", httpx.MockTransport(handler))
    re_ = providers.make("resend", "re-secret", httpx.MockTransport(handler))
    assert pm.send(msg, "row1") == "pm-1" and re_.send(msg, "row2") == "re-1"
    assert seen[0].headers["X-Postmark-Server-Token"] == "pm-secret"
    body = json.loads(seen[0].content)
    assert body["To"] == "maya@glowlab.co" and body["ReplyTo"] == "team@x.co" and body["Headers"][0]["Name"] == "List-Unsubscribe"
    assert seen[1].headers["Authorization"] == "Bearer re-secret" and seen[1].headers["Idempotency-Key"] == "row2"
    assert json.loads(seen[1].content)["tags"] == [{"name": "email", "value": "welcome_1"}]
    assert "secret" not in repr(pm) and "secret" not in repr(re_)
    assert providers.make("postmark", None) is None and isinstance(providers.make("outbox", None), providers.Outbox)

    def refuse(status):
        return httpx.MockTransport(lambda r: httpx.Response(status, json={"Message": "Sender not verified"}))

    for status, retry in ((422, False), (429, True), (503, True)):
        try:
            providers.make("postmark", "pm-secret", refuse(status)).send(msg, "r")
            raise AssertionError("should have failed")
        except providers.SendFailed as e:
            assert e.retry is retry and "pm-secret" not in str(e) and "Sender not verified" in str(e)


def test_unsubscribe_links_are_signed_per_person_and_one_click(env):
    factory, staff, vault, _ = env
    made = _signup(TestClient(staff.app))
    with factory() as s:
        u = s.scalar(select(m.User).where(m.User.email == "maya@glowlab.co"))
        uid = u.id
    url = links.unsubscribe_url("https://app.example", vault, uid)
    t = url.split("t=")[1]
    assert links.verify(vault, uid, t) and not links.verify(vault, uid, t[:-1] + "x")
    assert not links.verify(vault, "someone-else", t) and not links.verify(vault, uid, "")

    anon = TestClient(staff.app)
    assert anon.get(f"/email/unsubscribe?u={uid}&t=bad").status_code == 400
    ask = anon.get(f"/email/unsubscribe?u={uid}&t={t}")
    assert ask.status_code == 200 and "Stop reminders" in ask.text
    with factory() as s:
        assert s.get(m.User, uid).email_opt_out_at is None  # opening the link alone changes nothing
    assert anon.post(f"/email/unsubscribe?u={uid}&t=bad").status_code == 400
    assert anon.post(f"/email/unsubscribe?u={uid}&t={t}").status_code == 200
    with factory() as s:
        assert s.get(m.User, uid).email_opt_out_at is not None
    assert made["client_slug"]


def test_render_escapes_brand_text_and_keeps_a_plain_text_part():
    email = render.Email(subject="Hi", preheader="pre", blocks=[
        {"h": "Hello <script>"}, {"p": "A & B"}, {"stats": [("a year", "$1,200")]},
        {"button": ("Open", "https://app.example/audit/x")}])
    html = render.html(email, app_url="https://app.example", footer="F", unsubscribe_url="https://app.example/u")
    text = render.text(email, footer="F", unsubscribe_url="https://app.example/u")
    assert "<script>" not in html and "&lt;script&gt;" in html and "A &amp; B" in html
    assert "https://app.example/audit/x" in html and "https://app.example/u" in html
    assert "HELLO <SCRIPT>" in text and "a year: $1,200" in text and "https://app.example/u" in text


# ---------------------------------------------------------------- the sequence

def test_a_new_brand_gets_welcome_once_then_paced_connect_nudges_and_staff_hear(env):
    factory, staff, vault, _ = env
    _signup(TestClient(staff.app))
    start = utcnow().replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=1)  # daytime in New York
    with factory() as s:
        c = s.scalar(select(m.Client).where(m.Client.slug == "glow-lab"))
        c.created_at = start - timedelta(minutes=5)
        s.commit()

    _plan(factory, staff, vault, start)
    _plan(factory, staff, vault, start + timedelta(minutes=5))           # a second pass queues nothing new
    assert _keys(factory, "maya@glowlab.co") == ["welcome"]
    staff_mail = [e for e in _queued(factory) if e.kind == "staff"]
    assert [e.key.split(":")[0] for e in staff_mail] == ["staff_signup"] and staff_mail[0].to_email == "admin@demo.wavy.test"
    welcome = _queued(factory, to_email="maya@glowlab.co")[0]
    assert "/email/unsubscribe?u=" in welcome.html and welcome.data["headers"]["List-Unsubscribe-Post"]
    assert "unsubscribe" not in staff_mail[0].html.lower()
    # the demo brand Wavy manages for the client isn't self-serve: no onboarding email
    assert not [e for e in _queued(factory) if e.to_email == "owner@northwind.demo.test"]

    _plan(factory, staff, vault, start + timedelta(days=1, hours=1))
    assert _keys(factory, "maya@glowlab.co") == ["welcome", "connect_nudge"]
    _plan(factory, staff, vault, start + timedelta(days=3, hours=1))
    assert _keys(factory, "maya@glowlab.co")[-1] == "connect_help"
    # missed moments aren't sent late: connect_last is due at day 7; at day 10 it's stale and skipped
    _plan(factory, staff, vault, start + timedelta(days=10))
    assert "connect_last" not in _keys(factory, "maya@glowlab.co")


def test_nudges_respect_opt_out_pause_and_the_daily_gap(env):
    factory, staff, vault, _ = env
    _signup(TestClient(staff.app))
    start = utcnow().replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=1)
    with factory() as s:
        c = s.scalar(select(m.Client).where(m.Client.slug == "glow-lab"))
        c.created_at = start - timedelta(days=3, hours=1)       # connect_nudge and connect_help both due
        s.commit()
    _plan(factory, staff, vault, start)
    nudges = [e for e in _queued(factory, to_email="maya@glowlab.co") if e.kind == "nudge"]
    assert len(nudges) == 1                                      # one nudge per pass, and then 20 hours apart
    _plan(factory, staff, vault, start + timedelta(hours=2))
    assert len([e for e in _queued(factory, to_email="maya@glowlab.co") if e.kind == "nudge"]) == 1

    with factory() as s:
        u = s.scalar(select(m.User).where(m.User.email == "maya@glowlab.co"))
        u.email_opt_out_at = utcnow()
        s.commit()
    settings = _settings(staff)
    assert outbox.deliver(factory, settings, vault, now=start + timedelta(hours=3)) == {"suppressed": 1}

    with factory() as s:
        u = s.scalar(select(m.User).where(m.User.email == "maya@glowlab.co"))
        u.email_opt_out_at = None
        c = s.scalar(select(m.Client).where(m.Client.slug == "glow-lab"))
        c.settings = {**c.settings, "emails_paused": True}
        s.commit()
    before = len(_queued(factory, to_email="maya@glowlab.co"))
    _plan(factory, staff, vault, start + timedelta(days=4))         # connect_last is due, but the brand is paused
    assert len(_queued(factory, to_email="maya@glowlab.co")) == before
    with factory() as s:
        c = s.scalar(select(m.Client).where(m.Client.slug == "glow-lab"))
        c.settings = {**c.settings, "emails_paused": False}
        s.commit()
    _plan(factory, staff, vault, start + timedelta(days=4, hours=1))
    assert _keys(factory, "maya@glowlab.co")[-1] == "connect_last"


def test_deliver_sends_through_the_provider_or_waits_for_its_key(env):
    factory, staff, vault, _ = env
    _signup(TestClient(staff.app))
    now = utcnow()
    _plan(factory, staff, vault, now)
    settings = _settings(staff)
    waiting = dataclasses.replace(settings, email_provider="postmark")
    assert outbox.deliver(factory, waiting, vault, now=now, key_for=lambda org, p: None) == {"waiting_for_provider_key": 2}
    assert {e.status for e in _queued(factory)} == {"queued"}

    sent = []
    transport = httpx.MockTransport(lambda r: sent.append(json.loads(r.content)) or httpx.Response(200, json={"MessageID": "x"}))
    counts = outbox.deliver(factory, waiting, vault, now=now, key_for=lambda org, p: "pm-secret", transport=transport)
    assert counts == {"sent": 2} and {e.status for e in _queued(factory)} == {"sent"}
    assert {b["To"] for b in sent} == {"maya@glowlab.co", "admin@demo.wavy.test"}
    assert outbox.deliver(factory, waiting, vault, now=now, key_for=lambda org, p: "pm-secret", transport=transport) == {}

    # a queued email that couldn't go out in time is dropped, not sent days late
    with factory() as s:
        u = s.scalar(select(m.User).where(m.User.email == "maya@glowlab.co"))
        c = s.scalar(select(m.Client).where(m.Client.slug == "glow-lab"))
        outbox.queue(s, user=u, client=c, key="late", kind="transactional", email=render.Email("Late", "", [{"p": "x"}]),
                     settings=settings, vault=vault, now=now - timedelta(days=3))
        s.commit()
    assert outbox.deliver(factory, settings, vault, now=now) == {"skipped": 1}


def test_every_step_renders_for_a_brand_at_any_stage(env):
    factory, staff, vault, _ = env
    _signup(TestClient(staff.app))
    with factory() as s:
        c = s.scalar(select(m.Client).where(m.Client.slug == "glow-lab"))
        for key, step in lifecycle.STEPS.items():
            email = lifecycle.preview(s, c, key, "https://app.example")
            assert email.subject and email.blocks, key
            html = render.html(email, app_url="https://app.example", footer="F", unsubscribe_url=None)
            text = render.text(email, footer="F", unsubscribe_url=None)
            assert "None" not in text and "nan" not in text and "{" not in text and html, key


# ---------------------------------------------------------------- after the audit

def test_audit_ready_brand_approval_ship_and_measured_progress(env):
    factory, staff, vault, _ = env
    brand, slug, done = _brand_with_audit(env, providers=[], writers={})
    assert done["audit.analyze"].status == "succeeded", done["audit.analyze"].error
    now = utcnow()
    _plan(factory, staff, vault, now)
    keys = _keys(factory, "maya@glowlab.co")
    assert "welcome" in keys and "audit_ready" in keys and "connect_nudge" not in keys
    ready = next(e for e in _queued(factory, to_email="maya@glowlab.co") if e.key == "audit_ready")
    assert "#opportunities" in ready.html or f"/audit/{slug}" in ready.html
    assert any(e.key.startswith("staff_review_needed") for e in _queued(factory))

    p = brand.get(f"/v1/ops/clients/{slug}/progress").json()
    assert p["goal"] == 10000 and p["measured"] == 0 and p["next_step"]["key"] == "approve"

    # the brand's owner approves its own fix; staff-only routes stay closed to the brand
    cards = staff.get(f"/v1/ops/clients/{slug}/outcomes").json()["opportunities"]
    card = cards[0]
    res = brand.post(f"/v1/ops/clients/{slug}/opportunities/{card['id']}/approve", json={})
    assert res.status_code == 200, res.text
    outcome = res.json()
    assert outcome["owner"] == "Wavy Studios" and outcome["approved_by"]
    assert brand.get(f"/v1/ops/clients/{slug}/outcomes").status_code == 404
    assert brand.post(f"/v1/ops/outcomes/{outcome['id']}/shipped", json={}).status_code == 404
    assert brand.get(f"/v1/ops/clients/{slug}/mail").status_code == 404
    other = _as(staff, _signup(TestClient(staff.app), email="sam@other.co", brand="Other Co")["token"])
    assert other.post(f"/v1/ops/clients/{slug}/opportunities/{cards[1]['id']}/approve", json={}).status_code == 404

    p = brand.get(f"/v1/ops/clients/{slug}/progress").json()
    assert p["approved"] == 1 and p["live"] == 0 and p["next_step"]["key"] == "shipping"

    # staff mark it live ten days ago, with a target flow; the daily reading measures it
    shipped_at = (now - timedelta(days=10)).isoformat()
    assert staff.post(f"/v1/ops/outcomes/{outcome['id']}/shipped", json={"at": (now + timedelta(days=1)).isoformat()}).status_code == 422
    res = staff.post(f"/v1/ops/outcomes/{outcome['id']}/shipped", json={"at": shipped_at, "note": "built in Klaviyo"})
    assert res.status_code == 200, res.text
    with factory() as s:
        o = s.get(m.OpportunityOutcome, outcome["id"])
        o.targets = [{"flow_id": "F1", "name": "Welcome"}]
        c = s.scalar(select(m.Client).where(m.Client.slug == slug))
        assert impact.due(s, c.id, now) == [o]

        ship = outbox.aware(o.shipped_at)

        def read(start, end):
            if end <= ship:
                return {("F1", "M1"): {"recipients": 1000, "revenue": 1000.0}, ("F2", "M2"): {"recipients": 1000, "revenue": 2000.0}}
            return {("F1", "M1"): {"recipients": 1000, "revenue": 1500.0}, ("F2", "M2"): {"recipients": 1000, "revenue": 2000.0}}

        impact.measure(s, c, [o], read, now)
        s.commit()
    p = brand.get(f"/v1/ops/clients/{slug}/progress").json()
    row = p["fixes"][0]
    assert row["state"] == "measured" and row["lift"] == 500.0 and row["attributed"] == 1500.0
    assert p["measured"] == 500.0 and p["attributed"] == 1500.0 and not p["reached"]
    assert p["next_step"]["key"] in ("next_fix", "measuring")

    _plan(factory, staff, vault, now + timedelta(minutes=1))
    keys = _keys(factory, "maya@glowlab.co")
    assert "fix_live" not in keys          # it went live ten days ago; too late to announce
    assert "first_lift" in keys


def test_next_step_walks_unsized_cards_too_and_links_to_the_card():
    from types import SimpleNamespace
    cards = [{"id": "opp-1", "title": "Fix the order metric", "rank": 1},
             {"id": "opp-2", "title": "Welcome series", "rank": 2, "sizing": {"status": "sized", "annual": {"low": 6000, "high": 15000}}},
             {"id": "opp-3", "title": "Sunset", "rank": 3}]
    links = lifecycle.Links(connect="/c", questions="/q", workspace="/w", opportunities="/w#opportunities", console="/x")
    fixes = [{"opportunity_id": "opp-2", "shipped_at": "2026-01-01T00:00:00+00:00", "readable_from": "2026-01-08", "lift": 10.0}]
    b = SimpleNamespace(links=links, connected=utcnow(), problem=None, ready=utcnow(), summary={"opportunities": cards},
                        progress={"approved": 1, "live": 1, "measured_fixes": 1, "reached": False, "fixes": fixes})
    step = lifecycle.next_step(b)
    assert step["key"] == "next_fix" and step["title"].endswith("Fix the order metric") and step["url"] == "/w#opportunities/opp-1"
    assert "worth" not in step["detail"]
    b.progress = {"approved": 0, "fixes": []}
    first = lifecycle.next_step(b)
    assert first["key"] == "approve" and "Welcome series" in first["detail"] and "$6K–$15K a year" in first["detail"]


def test_cli_preview_writes_every_step_and_sends_nothing(env, tmp_path, monkeypatch, capsys):
    from wavyos import cli
    factory, staff, vault, db_file = env
    if not db_file.exists():
        return  # the CLI reads the database from settings; this check runs on the SQLite suite
    _signup(TestClient(staff.app))
    monkeypatch.setenv("WAVYOS_DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("WAVYOS_APP_URL", "https://app.example")
    out = tmp_path / "previews"
    assert cli.main(["emails", "preview", "--client", "glow-lab", "--out", str(out)]) == 0
    assert sorted(p.stem for p in out.glob("*.html")) == sorted(lifecycle.STEPS)
    assert "https://app.example/onboard?client=glow-lab" in (out / "welcome.html").read_text()
    assert "write_access" in capsys.readouterr().out
    assert _queued(factory) == []


def test_impact_compute_adjusts_for_the_account_trend_and_refuses_small_samples():
    targets = [{"flow_id": "F1", "message_id": "M1"}]
    before = {("F1", "M1"): {"recipients": 1000, "revenue": 1000.0}, ("F1", "M9"): {"recipients": 1000, "revenue": 500.0},
              ("F2", "M2"): {"recipients": 2000, "revenue": 2000.0}}
    after = {("F1", "M1"): {"recipients": 1000, "revenue": 1800.0}, ("F1", "M9"): {"recipients": 1000, "revenue": 9000.0},
             ("F2", "M2"): {"recipients": 2000, "revenue": 2400.0}}
    r = impact.compute(targets, before, after)
    # untouched flows grew 20%, so only the rest counts: (1.8 - 1.0 * 1.2) * 1000; the other step in F1 is excluded
    assert r["status"] == "measured" and r["trend"] == 1.2 and r["lift"] == 600.0 and r["attributed"] == 1800.0

    wild = {**after, ("F2", "M2"): {"recipients": 2000, "revenue": 20000.0}}
    assert impact.compute(targets, before, wild)["trend"] == 2.0                    # trend is capped
    small = {("F1", "M1"): {"recipients": 100, "revenue": 300.0}}
    assert impact.compute(targets, small, small)["status"] == "too_small"
    new = impact.compute(targets, {}, after)
    assert new["status"] == "pending" and new["method"] == "attributed_only" and new["lift"] is None

    res = {"data": {"attributes": {"results": [
        {"groupings": {"flow_id": "F1", "flow_message_id": "M1"}, "statistics": {"recipients": 10, "conversion_value": 5.5}},
        {"groupings": {"flow_id": "F1", "flow_message_id": "M1"}, "statistics": {"recipients": 5, "conversion_value": 1}}]}}}
    assert impact.parse_report(res) == {("F1", "M1"): {"recipients": 15.0, "revenue": 6.5}}
