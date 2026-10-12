"""Onboarding emails (outbound_emails, users.email_opt_out_at) and measured impact of live fixes
(impact_readings; targets and ship date on opportunity_outcomes).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-11 18:37:29.529862
"""
from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('outbound_emails',
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('client_id', sa.String(length=32), nullable=True),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('key', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('to_email', sa.String(length=320), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('html', sa.Text(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('send_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('provider_id', sa.String(length=200), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_outbound_emails_client_id_clients')),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_outbound_emails_org_id_organizations')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_outbound_emails_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outbound_emails')),
    sa.UniqueConstraint('user_id', 'key', name=op.f('uq_outbound_emails_user_id'))
    )
    with op.batch_alter_table('outbound_emails', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_outbound_emails_client_id'), ['client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_outbound_emails_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_outbound_emails_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_outbound_emails_user_id'), ['user_id'], unique=False)

    op.create_table('impact_readings',
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('client_id', sa.String(length=32), nullable=False),
    sa.Column('outcome_id', sa.String(length=32), nullable=False),
    sa.Column('method', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('before', sa.JSON(), nullable=False),
    sa.Column('after', sa.JSON(), nullable=False),
    sa.Column('trend', sa.Float(), nullable=True),
    sa.Column('lift', sa.Float(), nullable=True),
    sa.Column('attributed', sa.Float(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_impact_readings_client_id_clients')),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_impact_readings_org_id_organizations')),
    sa.ForeignKeyConstraint(['outcome_id'], ['opportunity_outcomes.id'], name=op.f('fk_impact_readings_outcome_id_opportunity_outcomes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_impact_readings'))
    )
    with op.batch_alter_table('impact_readings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_impact_readings_client_id'), ['client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_impact_readings_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_impact_readings_outcome_id'), ['outcome_id'], unique=False)

    with op.batch_alter_table('opportunity_outcomes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('targets', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
        batch_op.add_column(sa.Column('shipped_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('shipped_by', sa.String(length=120), nullable=False, server_default=''))

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email_opt_out_at', sa.DateTime(timezone=True), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('email_opt_out_at')

    with op.batch_alter_table('opportunity_outcomes', schema=None) as batch_op:
        batch_op.drop_column('shipped_by')
        batch_op.drop_column('shipped_at')
        batch_op.drop_column('targets')

    with op.batch_alter_table('impact_readings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_impact_readings_outcome_id'))
        batch_op.drop_index(batch_op.f('ix_impact_readings_org_id'))
        batch_op.drop_index(batch_op.f('ix_impact_readings_client_id'))

    op.drop_table('impact_readings')
    with op.batch_alter_table('outbound_emails', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_outbound_emails_user_id'))
        batch_op.drop_index(batch_op.f('ix_outbound_emails_status'))
        batch_op.drop_index(batch_op.f('ix_outbound_emails_org_id'))
        batch_op.drop_index(batch_op.f('ix_outbound_emails_client_id'))

    op.drop_table('outbound_emails')
