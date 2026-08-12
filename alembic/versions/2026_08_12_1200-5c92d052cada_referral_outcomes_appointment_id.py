"""referral_outcomes: nullable referral_request_id, add appointment_id

Revision ID: 5c92d052cada
Revises: f3a91c7d2b6e
Create Date: 2026-08-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5c92d052cada'
down_revision: Union[str, Sequence[str], None] = 'f3a91c7d2b6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Lets a consult outcome (and the completion-summary/MedicalRecord it
    generates — see app/services/referral_outcome.py) be recorded against a
    directly-booked appointment that never went through the referral
    workflow at all, not just a referral's own appointment. Exactly one of
    referral_request_id/appointment_id is expected to be set going forward
    (app-level invariant, not a DB constraint — see the model docstring).

    Note: autogenerate also proposes dropping the `checkpoint*` tables —
    those belong to LangGraph's AsyncPostgresSaver, created outside
    SQLAlchemy's Base.metadata — deliberately excluded, same as every
    migration before it.
    """
    op.alter_column('referral_outcomes', 'referral_request_id', existing_type=sa.INTEGER(), nullable=True)

    op.add_column('referral_outcomes', sa.Column('appointment_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f('fk_referral_outcomes_appointment_id_appointments'),
        'referral_outcomes', 'appointments', ['appointment_id'], ['id'],
    )
    op.create_unique_constraint(
        op.f('uq_referral_outcomes_appointment_id'), 'referral_outcomes', ['appointment_id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('uq_referral_outcomes_appointment_id'), 'referral_outcomes', type_='unique')
    op.drop_constraint(op.f('fk_referral_outcomes_appointment_id_appointments'), 'referral_outcomes', type_='foreignkey')
    op.drop_column('referral_outcomes', 'appointment_id')

    op.alter_column('referral_outcomes', 'referral_request_id', existing_type=sa.INTEGER(), nullable=False)
