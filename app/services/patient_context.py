from typing import Any, Dict

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.medical_record import MedicalRecord
from app.models.patient import Patient
from app.models.referral import ReferralDocument, ReferralRequest
from app.models.user import User
from app.services.record_scope import (
    appointment_visibility_filter,
    medical_record_visibility_filter,
    patient_visibility_filter,
)
from app.services.referral_scope import referral_visibility_filter

APPOINTMENTS_LIMIT = 10
MEDICAL_RECORDS_LIMIT = 5  # mirrors summarizer_node.PRIOR_VISITS_LIMIT
DOCUMENTS_LIMIT = 20


async def gather_patient_context(db: AsyncSession, current_user: User, patient_id: int) -> Dict[str, Any]:
    """Assembles the "everything about this patient the caller is allowed to
    see" view — the unified aggregation the dashboard's Patient Detail page
    is built on. Every sub-list below reuses the exact same visibility filter
    its own standalone list endpoint uses (record_scope.py / referral_scope.py)
    ANDed with this patient_id, so this is never broader than what the caller
    could already assemble by hand across GET /appointments/, GET
    /medical-records/, and GET /referral/requests/ — it's a convenience
    aggregation, not a new access boundary.
    """
    patient_scope = await patient_visibility_filter(db, current_user)
    patient_query = select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    if patient_scope is not None:
        patient_query = patient_query.where(patient_scope)
    patient = (await db.execute(patient_query)).scalar_one_or_none()
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    appt_scope = await appointment_visibility_filter(db, current_user)
    appt_query = select(Appointment).where(
        Appointment.patient_id == patient_id, Appointment.deleted_at.is_(None)
    )
    if appt_scope is not None:
        appt_query = appt_query.where(appt_scope)
    appointments = (
        await db.execute(appt_query.order_by(Appointment.appointment_datetime.desc()).limit(APPOINTMENTS_LIMIT))
    ).scalars().all()

    record_scope = await medical_record_visibility_filter(db, current_user)
    record_query = select(MedicalRecord).where(
        MedicalRecord.patient_id == patient_id, MedicalRecord.deleted_at.is_(None)
    )
    if record_scope is not None:
        record_query = record_query.where(record_scope)
    medical_records = (
        await db.execute(record_query.order_by(MedicalRecord.visit_date.desc()).limit(MEDICAL_RECORDS_LIMIT))
    ).scalars().all()

    referral_scope = await referral_visibility_filter(db, current_user)
    referral_query = select(ReferralRequest).where(
        ReferralRequest.patient_id == patient_id, ReferralRequest.deleted_at.is_(None)
    )
    if referral_scope is not None:
        referral_query = referral_query.where(referral_scope)
    referrals = (
        await db.execute(referral_query.order_by(ReferralRequest.id.desc()))
    ).scalars().all()

    # Referral documents (uploaded lab/imaging reports, letters) have no
    # visibility filter of their own — access is per-referral, via
    # `_get_scoped_referral` on GET /referral/requests/{id}/documents.
    # Scoping to the referral ids already filtered above keeps this the
    # same invariant as everything else here: never broader than what the
    # caller could already assemble by hand, one referral at a time.
    documents = []
    referral_ids = [referral.id for referral in referrals]
    if referral_ids:
        documents = (
            await db.execute(
                select(ReferralDocument)
                .where(ReferralDocument.referral_request_id.in_(referral_ids))
                .order_by(ReferralDocument.created_at.desc())
                .limit(DOCUMENTS_LIMIT)
            )
        ).scalars().all()

    care_team: Dict[int, str] = {}
    for referral in referrals:
        care_team.setdefault(referral.referring_doctor_id, "referring")
        if referral.specialist_id is not None:
            care_team[referral.specialist_id] = "specialist"
    for appointment in appointments:
        care_team.setdefault(appointment.doctor_id, "treating")
    for record in medical_records:
        care_team.setdefault(record.doctor_id, "treating")

    return {
        "patient_id": patient.id,
        "insurance": {
            "provider": patient.insurance_provider,
            "policy_number": patient.insurance_policy_number,
        },
        "care_team": [{"doctor_id": doctor_id, "role": role} for doctor_id, role in care_team.items()],
        "appointments": appointments,
        "medical_records": medical_records,
        "referrals": referrals,
        "documents": documents,
    }
