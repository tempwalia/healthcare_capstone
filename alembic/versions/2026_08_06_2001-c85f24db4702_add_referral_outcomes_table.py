"""add referral outcomes table

Revision ID: c85f24db4702
Revises: e7dc6e717530
Create Date: 2026-08-06 20:01:22.933473

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c85f24db4702'
down_revision: Union[str, Sequence[str], None] = 'e7dc6e717530'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Note: autogenerate also proposed dropping the `checkpoint*` tables —
    those belong to LangGraph's AsyncPostgresSaver (app/agents/checkpointer.py),
    created via its own `.setup()` outside SQLAlchemy's Base.metadata, so
    Alembic sees them as "unknown" and wants to drop them. Deliberately
    excluded from this migration.
    """
    op.create_table('referral_outcomes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('referral_request_id', sa.Integer(), nullable=False),
    sa.Column('recorded_by_user_id', sa.Integer(), nullable=False),
    sa.Column('symptoms', sa.Text(), nullable=True),
    sa.Column('diagnosis', sa.Text(), nullable=True),
    sa.Column('prescription', sa.Text(), nullable=True),
    sa.Column('follow_up_notes', sa.Text(), nullable=True),
    sa.Column('interaction_summary', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['recorded_by_user_id'], ['users.id'], name=op.f('fk_referral_outcomes_recorded_by_user_id_users')),
    sa.ForeignKeyConstraint(['referral_request_id'], ['referral_requests.id'], name=op.f('fk_referral_outcomes_referral_request_id_referral_requests')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_referral_outcomes')),
    sa.UniqueConstraint('referral_request_id', name=op.f('uq_referral_outcomes_referral_request_id'))
    )
    op.create_index(op.f('ix_referral_outcomes_id'), 'referral_outcomes', ['id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_referral_outcomes_id'), table_name='referral_outcomes')
    op.drop_table('referral_outcomes')
