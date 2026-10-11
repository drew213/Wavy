"""Alembic environment. Wavy OS always passes a live connection (wavyos.migrate.config)."""
from alembic import context

from wavyos import models  # noqa: F401  (registers every table)
from wavyos.db import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("run migrations through wavyos (`wavyos init-db` or wavyos.migrate), not the alembic CLI")

# render_as_batch: SQLite can't ALTER most things in place, so changes are written as table rebuilds.
context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
