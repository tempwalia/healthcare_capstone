from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment, AppointmentStatusEnum

# Statuses that represent a still-live commitment to see this doctor for
# this reason. COMPLETED/CANCELLED/NO_SHOW don't block a new booking — a
# cancelled or finished appointment isn't "still pending" in any sense a
# duplicate-booking check should care about. Filtered by the actual Enum
# members (not .value strings) — Appointment.status is a SQLAlchemy
# Enum(AppointmentStatusEnum) column bound by member, same convention
# app/agents/nodes/scheduling.py uses when writing one.
_ACTIVE_STATUSES = [
    AppointmentStatusEnum.SCHEDULED,
    AppointmentStatusEnum.CONFIRMED,
    AppointmentStatusEnum.IN_PROGRESS,
]


async def reject_if_duplicate_appointment(
    db: AsyncSession, *, patient_id: int, doctor_id: int, reason: str | None
) -> None:
    """Raises 409 if this patient already has a live (not completed/
    cancelled/no-show) appointment with this same doctor for the same
    reason/cause — the "don't let me double-book myself for the same thing"
    guard. A blank/None reason is never treated as a duplicate of anything
    (there's no "cause" to compare), so untagged bookings are never blocked."""
    if not reason or not reason.strip():
        return

    existing = (
        await db.execute(
            select(Appointment.id).where(
                Appointment.patient_id == patient_id,
                Appointment.doctor_id == doctor_id,
                Appointment.deleted_at.is_(None),
                Appointment.status.in_(_ACTIVE_STATUSES),
                func.lower(func.trim(Appointment.reason)) == reason.strip().lower(),
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You already have an upcoming appointment with this doctor for '{reason.strip()}' "
            f"(appointment #{existing}) — reschedule or cancel that one instead of booking another.",
        )
