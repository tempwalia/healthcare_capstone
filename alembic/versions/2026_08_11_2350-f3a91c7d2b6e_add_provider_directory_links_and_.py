"""add provider_directory_links table and appointments.referral_id column

Revision ID: f3a91c7d2b6e
Revises: 00e7bed49a5d
Create Date: 2026-08-11 23:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f3a91c7d2b6e'
down_revision: Union[str, Sequence[str], None] = '00e7bed49a5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Note: autogenerate also proposes dropping the `checkpoint*` tables —
    those belong to LangGraph's AsyncPostgresSaver (app/agents/checkpointer.py),
    created via its own `.setup()` outside SQLAlchemy's Base.metadata, so
    Alembic sees them as "unknown" and wants to drop them. Deliberately
    excluded from this migration, same as every migration before it.
    """
    op.create_table('provider_directory_links',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_system', sa.String(length=60), nullable=False),
    sa.Column('external_doctor_id', sa.Integer(), nullable=False),
    sa.Column('doctor_id', sa.Integer(), nullable=False),
    sa.Column('created_by_user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], name=op.f('fk_provider_directory_links_created_by_user_id_users')),
    sa.ForeignKeyConstraint(['doctor_id'], ['doctors.id'], name=op.f('fk_provider_directory_links_doctor_id_doctors')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_provider_directory_links')),
    sa.UniqueConstraint('source_system', 'external_doctor_id', name='uq_provider_directory_link_source_external')
    )
    op.create_index(op.f('ix_provider_directory_links_id'), 'provider_directory_links', ['id'], unique=False)

    op.add_column('appointments', sa.Column('referral_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f('fk_appointments_referral_id_referral_requests'),
        'appointments', 'referral_requests', ['referral_id'], ['id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_appointments_referral_id_referral_requests'), 'appointments', type_='foreignkey')
    op.drop_column('appointments', 'referral_id')

    op.drop_index(op.f('ix_provider_directory_links_id'), table_name='provider_directory_links')
    op.drop_table('provider_directory_links')
