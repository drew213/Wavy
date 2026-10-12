from __future__ import annotations

from alembic import command
from sqlalchemy import inspect, text

from wavyos import migrate
from wavyos.db import make_engine


def test_migrations_build_exactly_the_models(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    assert migrate.upgrade(engine) == f"database upgraded empty -> {migrate.head()}"
    assert migrate.current(engine) == migrate.head()
    assert migrate.differences(engine) == [], "models changed without a migration: run `wavyos db-revision -m ...`"
    assert migrate.upgrade(engine) == f"database at {migrate.head()}"


def test_a_database_made_before_migrations_is_adopted(tmp_path):
    # The live database: made by create_all before Alembic, so baseline-shaped with no alembic_version,
    # and from before the collector tables existed. Later revisions change those tables, so they must be
    # created in their baseline shape.
    collector = ("audit_artifacts", "audit_runs", "audit_orders", "metric_pins", "klaviyo_report_calls")
    engine = make_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        command.upgrade(migrate.config(conn), migrate.BASELINE)
        for table in ("alembic_version", *collector):
            conn.execute(text(f"DROP TABLE {table}"))
    assert "audit_orders" not in inspect(engine).get_table_names()

    assert migrate.upgrade(engine) == f"existing database adopted at {migrate.BASELINE}, now at {migrate.head()}"
    tables = set(inspect(engine).get_table_names())
    assert {*collector, "rate_limits"} <= tables
    assert "heartbeat_at" in {c["name"] for c in inspect(engine).get_columns("job_runs")}
    assert migrate.differences(engine) == []
