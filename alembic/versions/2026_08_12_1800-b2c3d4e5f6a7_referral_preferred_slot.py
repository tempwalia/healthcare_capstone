"""referral_requests.preferred_slot_id — lets a requester pick a specific
ScheduleSlot for a pre-chosen specialist instead of always auto-booking the
soonest one

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-12 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('referral_requests', sa.Column('preferred_slot_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f('fk_referral_requests_preferred_slot_id_schedule_slots'),
        'referral_requests', 'schedule_slots', ['preferred_slot_id'], ['id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_referral_requests_preferred_slot_id_schedule_slots'), 'referral_requests', type_='foreignkey')
    op.drop_column('referral_requests', 'preferred_slot_id')
