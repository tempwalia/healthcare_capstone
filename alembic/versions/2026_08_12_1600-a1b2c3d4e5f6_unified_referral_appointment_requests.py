"""unified referral/appointment requests: patient/doctor city, medical
record documents, nullable medical_record.doctor_id, medical_record_id on
referrals and appointments

Revision ID: a1b2c3d4e5f6
Revises: 5c92d052cada
Create Date: 2026-08-12 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '5c92d052cada'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Supports the unified "New Request" flow (referral + direct appointment
    combined): a simple city/region text field on Patient/Doctor for
    proximity-ranked doctor recommendation (no lat/long geocoding), a
    general-purpose medical_record_documents table (referrals already had
    their own ReferralDocument — medical records had nothing), Medical
    Record.doctor_id becoming optional (a patient's standalone document
    upload has no treating doctor to attribute it to), and a nullable
    medical_record_id on both referral_requests and appointments so a
    request can carry the record the user picked/uploaded when creating it.
    """
    op.add_column('patients', sa.Column('city', sa.String(length=100), nullable=True))
    op.add_column('doctors', sa.Column('city', sa.String(length=100), nullable=True))

    op.alter_column('medical_records', 'doctor_id', existing_type=sa.INTEGER(), nullable=True)

    op.create_table(
        'medical_record_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('medical_record_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('storage_path', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(
            ['medical_record_id'], ['medical_records.id'],
            name=op.f('fk_medical_record_documents_medical_record_id_medical_records'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_medical_record_documents')),
    )
    op.create_index(op.f('ix_medical_record_documents_id'), 'medical_record_documents', ['id'], unique=False)

    op.add_column('referral_requests', sa.Column('medical_record_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f('fk_referral_requests_medical_record_id_medical_records'),
        'referral_requests', 'medical_records', ['medical_record_id'], ['id'],
    )

    op.add_column('appointments', sa.Column('medical_record_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f('fk_appointments_medical_record_id_medical_records'),
        'appointments', 'medical_records', ['medical_record_id'], ['id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_appointments_medical_record_id_medical_records'), 'appointments', type_='foreignkey')
    op.drop_column('appointments', 'medical_record_id')

    op.drop_constraint(op.f('fk_referral_requests_medical_record_id_medical_records'), 'referral_requests', type_='foreignkey')
    op.drop_column('referral_requests', 'medical_record_id')

    op.drop_index(op.f('ix_medical_record_documents_id'), table_name='medical_record_documents')
    op.drop_table('medical_record_documents')

    op.alter_column('medical_records', 'doctor_id', existing_type=sa.INTEGER(), nullable=False)

    op.drop_column('doctors', 'city')
    op.drop_column('patients', 'city')
