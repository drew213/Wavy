from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from wavyos import demo
from wavyos import models as m
from wavyos.api import create_app
from wavyos.db import create_all, make_engine, make_sessionmaker, utcnow


@pytest.fixture()
def env(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    create_all(eng)
    factory = make_sessionmaker(eng)
    token = demo.build(factory)
    app = create_app(factory=factory)
    admin = TestClient(app)
    admin.headers["Authorization"] = "Bearer " + token
    return factory, app, admin


def _redeem(app, link: str, password: str):
    return TestClient(app).post("/v1/ops/password-link", json={"token": link.split("t=", 1)[1], "password": password})


def test_an_admin_adds_a_teammate_who_sets_a_password_and_sees_every_client(env):
    _, app, admin = env
    added = admin.post("/v1/ops/team", json={"email": "Sam@Example.test", "name": "Sam Rivera", "role": "org_admin"})
    assert added.status_code == 200, added.text
    body = added.json()
    assert body["person"]["email"] == "sam@example.test" and body["person"]["roles"] == ["org_admin"]
    assert body["person"]["has_password"] is False and body["link"].startswith("/welcome?t=wpl_")

    link_token = body["link"].split("t=", 1)[1]
    assert TestClient(app).get("/v1/ops/me", headers={"Authorization": "Bearer " + link_token}).status_code == 401

    done = _redeem(app, body["link"], "a-long-password-1")
    assert done.status_code == 200, done.text
    sam = TestClient(app)
    sam.headers["Authorization"] = "Bearer " + done.json()["token"]
    assert sam.get("/v1/ops/me").json()["staff"] is True
    assert {c["slug"] for c in sam.get("/v1/ops/overview").json()["clients"]} == \
        {c["slug"] for c in admin.get("/v1/ops/overview").json()["clients"]}

    assert _redeem(app, body["link"], "another-password-2").status_code == 409
    assert TestClient(app).post("/v1/ops/login", json={"email": "sam@example.test", "password": "a-long-password-1"}).status_code == 200
    team = {p["email"]: p for p in admin.get("/v1/ops/team").json()["people"]}
    assert team["sam@example.test"]["has_password"] is True


def test_a_new_link_cancels_the_last_and_resets_an_existing_password(env):
    factory, app, admin = env
    first = admin.post("/v1/ops/team", json={"email": "sam@example.test", "name": "Sam", "role": "org_admin"}).json()
    assert _redeem(app, first["link"], "first-password-1").status_code == 200
    again = admin.post("/v1/ops/team", json={"email": "sam@example.test", "name": "Sam", "role": "org_admin"}).json()
    assert again["person"]["id"] == first["person"]["id"]
    with factory() as s:
        assert s.query(m.Membership).filter_by(user_id=first["person"]["id"], client_id=None).count() == 1

    older = admin.post(f"/v1/ops/team/{first['person']['id']}/link").json()["link"]
    newer = admin.post(f"/v1/ops/team/{first['person']['id']}/link").json()["link"]
    assert _redeem(app, older, "second-password-2").status_code == 409
    assert _redeem(app, newer, "second-password-2").status_code == 200
    login = lambda pw: TestClient(app).post("/v1/ops/login", json={"email": "sam@example.test", "password": pw}).status_code
    assert login("first-password-1") == 401 and login("second-password-2") == 200


def test_links_expire_and_short_passwords_keep_the_link(env):
    factory, app, admin = env
    link = admin.post("/v1/ops/team", json={"email": "sam@example.test", "name": "Sam", "role": "org_admin"}).json()["link"]
    assert _redeem(app, link, "short").status_code == 422
    with factory() as s:
        row = s.query(m.ApiToken).filter_by(label="set-password link", revoked_at=None).one()
        row.expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert _redeem(app, link, "a-long-password-1").status_code == 409
    assert TestClient(app).post("/v1/ops/password-link", json={"token": "wpl_made-up", "password": "a-long-password-1"}).status_code == 409


def test_the_cli_adds_a_teammate_and_prints_links(tmp_path, monkeypatch, capsys):
    from wavyos import accounts
    from wavyos.cli import main
    monkeypatch.setenv("WAVYOS_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("WAVYOS_ADMIN_EMAIL", "admin@example.test")
    monkeypatch.setenv("WAVYOS_ADMIN_PASSWORD", "admin-password-123")
    assert main(["init-db"]) == 0 and main(["ensure-admin"]) == 0
    assert main(["team", "add", "--as", "admin@example.test", "--email", "sam@example.test", "--name", "Sam",
                 "--site", "https://site.example"]) == 0
    assert main(["team", "link", "--email", "admin@example.test", "--site", "https://site.example"]) == 0
    links = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("https://site.example/welcome?t=wpl_")]
    assert len(links) == 2
    with make_sessionmaker(make_engine(f"sqlite:///{tmp_path / 'cli.db'}"))() as s:
        user, _ = accounts.redeem_password_link(s, links[1].split("t=", 1)[1], "admin-password-456")
        s.commit()
        assert user.email == "admin@example.test"
        accounts.sign_in(s, "admin@example.test", "admin-password-456")


def test_only_org_admins_manage_the_team(env):
    _, app, admin = env
    lead = admin.post("/v1/ops/team", json={"email": "lead@example.test", "name": "Lee", "role": "account_lead"}).json()
    token = _redeem(app, lead["link"], "a-long-password-1").json()["token"]
    as_lead = TestClient(app)
    as_lead.headers["Authorization"] = "Bearer " + token
    assert as_lead.get("/v1/ops/me").json()["staff"] is True
    assert as_lead.get("/v1/ops/team").status_code == 404
    assert as_lead.post("/v1/ops/team", json={"email": "x@example.test", "name": "X", "role": "org_admin"}).status_code == 404
    assert admin.post("/v1/ops/team", json={"email": "x@example.test", "name": "X", "role": "client_owner"}).status_code == 422
    assert admin.post("/v1/ops/team", json={"email": "not-an-email", "name": "X", "role": "org_admin"}).status_code == 422
