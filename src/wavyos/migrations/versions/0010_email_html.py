"""Live email HTML, stored once per brand per version, for journey map previews.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-11 17:39:35.620278
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('email_html',
    sa.Column('org_id', sa.String(length=32), nullable=False),
    sa.Column('client_id', sa.String(length=32), nullable=False),
    sa.Column('template_id', sa.String(length=64), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('html', sa.Text(), nullable=False),
    sa.Column('bytes', sa.Integer(), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_email_html_client_id_clients')),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_email_html_org_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_email_html')),
    sa.UniqueConstraint('client_id', 'sha256', name=op.f('uq_email_html_client_id'))
    )
    with op.batch_alter_table('email_html', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_html_client_id'), ['client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_html_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_html_template_id'), ['template_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('email_html', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_html_template_id'))
        batch_op.drop_index(batch_op.f('ix_email_html_org_id'))
        batch_op.drop_index(batch_op.f('ix_email_html_client_id'))

    op.drop_table('email_html')
