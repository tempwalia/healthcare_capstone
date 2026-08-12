from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time_utils import ensure_aware
from app.models.appointment import Appointment
from app.models.schedule import ScheduleSlot
from app.services.appointment_dedup import reject_if_duplicate_appointment


async def find_soonest_open_slot(db: AsyncSession, doctor_id: int) -> Optional[ScheduleSlot]:
    return (
        await db.execute(
            select(ScheduleSlot).where(
                ScheduleSlot.doctor_id == doctor_id,
                ScheduleSlot.is_booked.is_(False),
                ScheduleSlot.deleted_at.is_(None),
                ScheduleSlot.starts_at > datetime.now(timezone.utc),
            ).order_by(ScheduleSlot.starts_at.asc()).limit(1)
        )
    ).scalar_one_or_none()


async def find_preferred_or_soonest_slot(
    db: AsyncSession, *, doctor_id: int, preferred_slot_id: Optional[int] = None
) -> Optional[ScheduleSlot]:
    """The requester's specifically-picked slot, if it's still valid (same
    doctor, still open, not in the past) — falls back to the soonest open
    slot otherwise, same as if nothing had been picked. Re-validating here
    (not just trusting the create-time check in submit_referral) matters
    because booking happens in a background task some time after the
    request was validated — someone else could have taken the slot meanwhile."""
    if preferred_slot_id is not None:
        slot = await db.get(ScheduleSlot, preferred_slot_id)
        if (
            slot is not None and slot.deleted_at is None and slot.doctor_id == doctor_id
            and not slot.is_booked and ensure_aware(slot.starts_at) > datetime.now(timezone.utc)
        ):
            return slot
    return await find_soonest_open_slot(db, doctor_id)


async def book_slot_for_patient(
    db: AsyncSession,
    *,
    slot: ScheduleSlot,
    patient_id: int,
    reason: Optional[str] = None,
    referral_id: Optional[int] = None,
    medical_record_id: Optional[int] = None,
) -> Appointment:
    """Core booking logic shared by `POST /schedule/slots/{slot_id}/book`
    and the referral workflow's `book_real_appointment_node`. Caller owns
    slot-validity checks (not already booked, not in the past) and
    committing afterward."""
    await reject_if_duplicate_appointment(db, patient_id=patient_id, doctor_id=slot.doctor_id, reason=reason)

    appointment = Appointment(
        patient_id=patient_id,
        doctor_id=slot.doctor_id,
        appointment_datetime=slot.starts_at,
        reason=reason,
        referral_id=referral_id,
        medical_record_id=medical_record_id,
    )
    db.add(appointment)
    await db.flush()

    slot.is_booked = True
    slot.appointment_id = appointment.id

    return appointment
