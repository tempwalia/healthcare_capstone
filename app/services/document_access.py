from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.medical_record import MedicalRecord
from app.models.referral import ReferralRequest
from app.models.user import User
from app.services.record_scope import appointment_visibility_filter, medical_record_visibility_filter
from app.services.referral_scope import referral_visibility_filter


async def user_can_view_medical_record(db: AsyncSession, current_user: User, record_id: int) -> bool:
    """A caller can view a medical record either through the record's own
    visibility scope (its own patient/doctor, or a view_all role), OR
    because it's attached to a referral or appointment they can already
    see — e.g. a specialist assigned to a referral should be able to open
    the record the patient attached when requesting it, even though they
    aren't that record's `doctor_id`. Each check is independently
    permission-gated (`referral_visibility_filter`/`appointment_visibility_filter`
    raise if the caller lacks even the base view permission) — caught and
    treated as "this path doesn't grant access" rather than failing the
    whole determination, since a caller missing one permission may still
    have another."""
    try:
        scope = await medical_record_visibility_filter(db, current_user)
        query = select(MedicalRecord.id).where(MedicalRecord.id == record_id, MedicalRecord.deleted_at.is_(None))
        if scope is not None:
            query = query.where(scope)
        if (await db.execute(query)).first() is not None:
            return True
    except HTTPException:
        pass

    try:
        ref_scope = await referral_visibility_filter(db, current_user)
        query = select(ReferralRequest.id).where(
            ReferralRequest.medical_record_id == record_id, ReferralRequest.deleted_at.is_(None)
        )
        if ref_scope is not None:
            query = query.where(ref_scope)
        if (await db.execute(query)).first() is not None:
            return True
    except HTTPException:
        pass

    try:
        appt_scope = await appointment_visibility_filter(db, current_user)
        query = select(Appointment.id).where(
            Appointment.medical_record_id == record_id, Appointment.deleted_at.is_(None)
        )
        if appt_scope is not None:
            query = query.where(appt_scope)
        if (await db.execute(query)).first() is not None:
            return True
    except HTTPException:
        pass

    return False
