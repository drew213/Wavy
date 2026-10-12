"""Browser sign-ins expire after 30 days; ones issued before this get 30 days from the upgrade.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-11 18:26:03.033381
"""
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('api_tokens', schema=None) as batch_op:
        batch_op.add_column(sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
    tokens = sa.table('api_tokens', sa.column('label', sa.String), sa.column('revoked_at', sa.DateTime(timezone=True)),
                      sa.column('expires_at', sa.DateTime(timezone=True)))
    op.execute(tokens.update().where(tokens.c.label == 'browser sign-in', tokens.c.revoked_at.is_(None))
               .values(expires_at=datetime.now(timezone.utc) + timedelta(days=30)))


def downgrade() -> None:
    with op.batch_alter_table('api_tokens', schema=None) as batch_op:
        batch_op.drop_column('expires_at')
