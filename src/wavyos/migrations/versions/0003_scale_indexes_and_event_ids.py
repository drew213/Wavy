"""indexes for the job queue and orders, 64-bit event ids

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11 14:29:33.281783
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('audit_orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_orders_placed_at'))
        batch_op.create_index('ix_audit_orders_client_placed', ['client_id', 'placed_at'], unique=False)

    with op.batch_alter_table('job_runs', schema=None) as batch_op:
        batch_op.create_index('ix_job_runs_status_due', ['status', 'due_at'], unique=False)

    # Written by hand: autogenerate runs on SQLite, where the column stays INTEGER.
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column('events', 'id', type_=sa.BigInteger(), existing_type=sa.Integer(), existing_nullable=False)
        op.execute("ALTER SEQUENCE IF EXISTS events_id_seq AS BIGINT")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER SEQUENCE IF EXISTS events_id_seq AS INTEGER")
        op.alter_column('events', 'id', type_=sa.Integer(), existing_type=sa.BigInteger(), existing_nullable=False)

    with op.batch_alter_table('job_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_job_runs_status_due')

    with op.batch_alter_table('audit_orders', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_orders_client_placed')
        batch_op.create_index(batch_op.f('ix_audit_orders_placed_at'), ['placed_at'], unique=False)
