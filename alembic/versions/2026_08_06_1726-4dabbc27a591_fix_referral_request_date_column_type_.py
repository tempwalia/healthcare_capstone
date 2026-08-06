"""fix referral request_date column type to Date

Revision ID: 4dabbc27a591
Revises: 2d4a9f348e62
Create Date: 2026-08-06 17:26:50.279923

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4dabbc27a591'
down_revision: Union[str, Sequence[str], None] = '2d4a9f348e62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # request_date was wrongly modeled as timestamptz (Phase 4 bug — Postgres
    # shifted stored midnight-UTC values by the session timezone offset,
    # which then failed strict `date` response validation). Explicit USING
    # cast since this table may already have rows.
    op.alter_column('referral_requests', 'request_date',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               type_=sa.Date(),
               existing_nullable=False,
               postgresql_using='request_date::date')


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('referral_requests', 'request_date',
               existing_type=sa.Date(),
               type_=postgresql.TIMESTAMP(timezone=True),
               existing_nullable=False,
               postgresql_using='request_date::timestamptz')
