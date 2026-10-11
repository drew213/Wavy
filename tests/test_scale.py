"""The pieces that let one deployment carry many brands: race-safe event chains, live-job heartbeats,
concurrent workers and pruning of raw audit data."""
from __future__ import annotations

import os
import threading
import time
from datetime import timedelta, timezone

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import func, select

from wavyos import events, housekeeping, jobs, tenancy
from wavyos import models as m
from wavyos.db import create_all, make_engine, make_sessionmaker, utcnow
from wavyos.scope import scope_for_user

PG_URL = os.environ.get("WAVYOS_TEST_POSTGRES_URL")


def test_an_event_that_loses_the_seq_race_links_onto_the_winner(session, world, monkeypatch):
    w = world
    scope = w.user(w.admin)
    events.record(session, scope, "note.added", "x", "first")
    stale = events._head(session, w.org.id, w.a.id)
    events.record(session, scope, "note.added", "x", "the other writer")  # takes the seq `stale` points at

    real, calls = events._head, []

    def head(*args):
        calls.append(1)
        return stale if len(calls) == 1 else real(*args)

    monkeypatch.setattr(events, "_head", head)
    ev = events.record(session, scope, "note.added", "x", "late writer")
    assert len(calls) == 2 and ev.seq == stale[0] + 1
    session.commit()
    assert events.verify_chain(session, w.org.id, w.a.id) == (True, None)


def _running(session, client, *, started_ago, beat_ago=None, attempts=1) -> m.JobRun:
    now = utcnow()
    run = m.JobRun(org_id=client.org_id, client_id=client.id, job_key="test.job", run_key=f"t:{utcnow().timestamp()}",
                   due_at=now, status="running", attempts=attempts, started_at=now - started_ago,
                   heartbeat_at=None if beat_ago is None else now - beat_ago)
    session.add(run)
    session.commit()
    return run


def test_only_runs_whose_heartbeat_stopped_are_requeued(session, world):
    alive = _running(session, world.a, started_ago=timedelta(hours=3), beat_ago=timedelta(minutes=1))
    dead = _running(session, world.a, started_ago=timedelta(hours=3), beat_ago=timedelta(minutes=30))
    from_before_heartbeats = _running(session, world.a, started_ago=timedelta(hours=2))
    out_of_attempts = _running(session, world.a, started_ago=timedelta(hours=3), beat_ago=timedelta(minutes=30), attempts=3)

    assert jobs.requeue_stuck(session) == 2
    session.commit()
    for run in (alive, dead, from_before_heartbeats, out_of_attempts):
        session.refresh(run)
    assert alive.status == "running"
    assert dead.status == from_before_heartbeats.status == "queued"
    assert out_of_attempts.status == "failed" and "stopped responding" in out_of_attempts.error


@pytest.fixture()
def file_factory(tmp_path):
    """A database other threads can reach (an in-memory SQLite is private to one thread)."""
    engine = make_engine(f"sqlite:///{tmp_path / 'shared.db'}")
    create_all(engine)
    factory = make_sessionmaker(engine)
    with factory() as s:
        org, admin = tenancy.bootstrap_org(s, name="Agency", slug="agency", admin_email="a@agency.test", admin_name="A")
        scope = scope_for_user(s, admin, None)
        clients = [tenancy.create_client(s, scope, name=f"Brand {i}", slug=f"brand-{i}") for i in range(3)]
        s.commit()
    yield factory, clients
    engine.dispose()


def test_a_running_job_keeps_its_heartbeat_fresh(file_factory):
    factory, clients = file_factory
    with factory() as s:
        run = _running(s, clients[0], started_ago=timedelta(hours=2), beat_ago=timedelta(hours=2))
    with jobs.heartbeat(factory, run.id, every=0.05):
        time.sleep(0.4)
    with factory() as s:
        beat = s.get(m.JobRun, run.id).heartbeat_at
        assert jobs.requeue_stuck(s) == 0
    beat = beat.replace(tzinfo=timezone.utc) if beat.tzinfo is None else beat  # SQLite returns naive UTC
    assert beat > utcnow() - timedelta(minutes=1)


@pytest.mark.parametrize("same_brand", [False, True])
@pytest.mark.skipif(not PG_URL, reason="concurrent writers need Postgres (set WAVYOS_TEST_POSTGRES_URL)")
def test_threads_run_jobs_at_the_same_time(file_factory, monkeypatch, same_brand):
    # Same brand too: the runner commits its job.run event before the handler starts, so the brand's
    # event chain isn't held for the length of a job.
    factory, clients = file_factory
    if same_brand:
        clients = [clients[0]] * 3
    together = threading.Barrier(3, timeout=10)  # passes only if three jobs are inside their handlers at once

    def handler(ctx: jobs.JobContext) -> jobs.JobOutcome:
        together.wait()
        return jobs.JobOutcome("met the others", no_action_reason="test job")

    defn = jobs.JobDefinition("test.parallel", "Parallel", "event", "analyst", "", "", "", handler, ("t",))
    monkeypatch.setitem(jobs.REGISTRY, defn.key, defn)
    with factory() as s:
        for i, client in enumerate(clients):
            jobs._enqueue(s, s.get(m.Client, client.id), defn, f"p{i}", utcnow(), {})
        s.commit()
    done = jobs.run_pending(factory, limit=10, threads=3)
    assert sorted(r.status for r in done) == ["no_action"] * 3, [r.error for r in done]


def test_allowed_reads_are_not_logged_but_denials_are(session, world):
    from wavyos.policy import authorize
    before = events.count(session, world.org.id, world.a.id)
    assert authorize(session, world.user(world.lead), "data.read").allowed
    assert events.count(session, world.org.id, world.a.id) == before
    assert not authorize(session, world.user(world.approver, world.a), "standard.publish").allowed
    assert events.count(session, world.org.id, world.a.id) == before + 1


def _queries(engine, fn) -> int:
    n = [0]

    def tick(*_):
        n[0] += 1

    sa_event.listen(engine, "before_cursor_execute", tick)
    try:
        fn()
    finally:
        sa_event.remove(engine, "before_cursor_execute", tick)
    return n[0]


def test_the_scheduler_does_not_query_per_brand(session, world, monkeypatch):
    def handler(ctx):
        return jobs.JobOutcome("ok", no_action_reason="test")

    monkeypatch.setattr(jobs, "REGISTRY", {
        key: jobs.JobDefinition(key, key, key.split(".")[1], "analyst", "", "", "", handler)
        for key in ("test.daily", "test.weekly")})
    engine = session.get_bind()
    few = _queries(engine, lambda: jobs.enqueue_due(session))
    scope = world.user(world.admin, None)
    for i in range(40):
        tenancy.create_client(session, scope, name=f"Extra {i}", slug=f"extra-{i}")
    session.flush()
    assert len(jobs.enqueue_due(session)) == 80
    many = _queries(engine, lambda: jobs.enqueue_due(session))
    assert many <= few + 1 and jobs.enqueue_due(session) == []


@pytest.fixture()
def console(tmp_path):
    from fastapi.testclient import TestClient
    from wavyos import demo
    from wavyos.api import create_app
    engine = make_engine(f"sqlite:///{tmp_path / 'console.db'}")  # a file: requests run on another thread
    create_all(engine)
    factory = make_sessionmaker(engine)
    token = demo.build(factory)
    client = TestClient(create_app(factory=factory))
    client.headers["Authorization"] = "Bearer " + token
    yield engine, factory, client
    engine.dispose()


def test_console_lists_cost_the_same_queries_for_any_number_of_brands(console):
    engine, factory, c = console
    paths = ["/v1/ops/overview", "/v1/ops/approvals", "/v1/ops/jobs", "/v1/ops/executions", "/v1/ops/events"]

    def cost() -> dict[str, int]:
        return {p: _queries(engine, lambda p=p: c.get(p).raise_for_status()) for p in paths}

    before = cost()
    with factory() as s:
        admin = s.scalar(select(m.User).where(m.User.email == "admin@demo.wavy.test"))
        scope = scope_for_user(s, admin, None)
        for i in range(25):
            tenancy.create_client(s, scope, name=f"Brand {i}", slug=f"brand-{i}")
        s.commit()
    assert len(c.get("/v1/ops/overview").json()["clients"]) == 26
    assert cost() == before


def test_error_reporting_is_off_without_a_dsn(monkeypatch):
    from wavyos import monitoring
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert monitoring.init() is False
    monitoring.report(RuntimeError("not sent anywhere"))


def test_old_runs_lose_their_raw_artifacts_but_keep_their_history(session, world):
    c = world.a
    runs = []
    for i, status in enumerate(["ready", "failed", "partial", "ready", "ready", "collecting"]):
        run = m.AuditRun(org_id=c.org_id, client_id=c.id, status=status, started_by="test",
                         created_at=utcnow() - timedelta(days=60 - i))
        session.add(run)
        session.flush()
        session.add(m.AuditArtifact(org_id=c.org_id, client_id=c.id, run_id=run.id, name="api", payload={"n": i}))
        runs.append(run)
    session.commit()

    assert housekeeping.prune_audit_artifacts(session) == 2
    session.commit()
    kept = set(session.scalars(select(m.AuditArtifact.run_id)))
    assert kept == {r.id for r in runs[2:]}  # newest three finished, plus the one still collecting
    assert session.scalar(select(func.count()).select_from(m.AuditRun)) == 6
