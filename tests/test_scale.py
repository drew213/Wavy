"""The pieces that let one deployment carry many brands: race-safe event chains, live-job heartbeats,
concurrent workers and pruning of raw audit data."""
from __future__ import annotations

import os
import threading
import time
from datetime import timedelta, timezone

import pytest
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


@pytest.mark.skipif(not PG_URL, reason="concurrent writers need Postgres (set WAVYOS_TEST_POSTGRES_URL)")
def test_threads_run_jobs_for_different_brands_at_the_same_time(file_factory, monkeypatch):
    # Jobs for one brand take turns: each writes to the brand's event chain, which has one writer at a time.
    factory, clients = file_factory
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
