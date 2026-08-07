"""add notifications table

Revision ID: 00e7bed49a5d
Revises: c85f24db4702
Create Date: 2026-08-07 10:13:01.771759

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '00e7bed49a5d'
down_revision: Union[str, Sequence[str], None] = 'c85f24db4702'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Note: autogenerate also proposed dropping the `checkpoint*` tables —
    those belong to LangGraph's AsyncPostgresSaver (app/agents/checkpointer.py),
    created via its own `.setup()` outside SQLAlchemy's Base.metadata, so
    Alembic sees them as "unknown" and wants to drop them. Deliberately
    excluded from this migration, same as the referral_outcomes migration.
    """
    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('referral_id', sa.Integer(), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['referral_id'], ['referral_requests.id'], name=op.f('fk_notifications_referral_id_referral_requests')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications'))
    )
    op.create_index(op.f('ix_notifications_id'), 'notifications', ['id'], unique=False)
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_id'), table_name='notifications')
    op.drop_table('notifications')
