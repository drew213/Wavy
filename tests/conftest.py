from __future__ import annotations

import hashlib
import os

import pytest

from wavyos import db as wdb
from wavyos import models as m
from wavyos import tenancy
from wavyos.db import create_all, make_engine, make_sessionmaker
from wavyos.scope import scope_for_agent, scope_for_user
from wavyos.vault import LocalKeyProvider, Vault

PG_URL = os.environ.get("WAVYOS_TEST_POSTGRES_URL")  # e.g. postgresql+psycopg://localhost/wavyos_test


@pytest.fixture(autouse=True)
def _postgres_instead_of_sqlite(monkeypatch, request):
    """With WAVYOS_TEST_POSTGRES_URL set, every SQLite database a test opens becomes its own
    Postgres schema, so the whole suite runs against Postgres unchanged."""
    if not PG_URL:
        yield
        return
    import sqlalchemy as sa
    real = wdb.create_engine
    admin = real(PG_URL, future=True, isolation_level="AUTOCOMMIT")
    made: dict[str, str] = {}
    engines = []

    def fake_create_engine(url, **kw):
        url = str(url)
        if not url.startswith("sqlite"):
            return real(url, **kw)
        key = url if url != "sqlite://" else f"mem{len(made)}"
        if key not in made:
            made[key] = "t_" + hashlib.sha1(f"{request.node.nodeid}|{key}".encode()).hexdigest()[:20]
            with admin.connect() as c:
                c.execute(sa.text(f'DROP SCHEMA IF EXISTS "{made[key]}" CASCADE'))
                c.execute(sa.text(f'CREATE SCHEMA "{made[key]}"'))
        engines.append(real(PG_URL, future=True, connect_args={"options": f"-csearch_path={made[key]}"}))
        return engines[-1]

    monkeypatch.setattr(wdb, "create_engine", fake_create_engine)
    yield
    for e in engines:
        e.dispose()
    with admin.connect() as c:
        for schema in made.values():
            c.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture()
def factory():
    engine = make_engine("sqlite://")
    create_all(engine)
    return make_sessionmaker(engine)


@pytest.fixture()
def session(factory):
    s = factory()
    yield s
    s.close()


@pytest.fixture()
def world(session):
    """An agency with one admin, one account lead, two clients, a client approver on client A."""
    org, admin = tenancy.bootstrap_org(session, name="Wavy Studios", slug="wavy", admin_email="admin@wavy.test", admin_name="Admin")
    admin_scope = scope_for_user(session, admin, None)
    a = tenancy.create_client(session, admin_scope, name="Brand A", slug="brand-a", timezone="America/Los_Angeles")
    b = tenancy.create_client(session, admin_scope, name="Brand B", slug="brand-b", timezone="America/New_York")
    lead = tenancy.add_user(session, admin_scope, email="lead@wavy.test", name="Lead")
    tenancy.grant(session, admin_scope, user_id=lead.id, role="account_lead", client_id=None)
    approver = tenancy.add_user(session, admin_scope, email="owner@brand-a.test", name="Owner", kind="client")
    tenancy.grant(session, admin_scope, user_id=approver.id, role="client_approver", client_id=a.id)
    session.flush()
    agents = {ag.role: ag for ag in session.query(m.Agent).filter_by(org_id=org.id)}

    class W:
        pass
    w = W()
    w.org, w.admin, w.lead, w.approver, w.a, w.b, w.agents = org, admin, lead, approver, a, b, agents
    w.user = lambda user, client=a: scope_for_user(session, user, client.id if client else None)
    w.agent = lambda role, client=a: scope_for_agent(session, agents[role], client.id if client else None)
    return w


@pytest.fixture()
def vault(tmp_path):
    return Vault(LocalKeyProvider(tmp_path / "master.key"))
