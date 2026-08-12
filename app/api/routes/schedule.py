from datetime import datetime, time, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_active_user, require_permission
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.core.time_utils import ensure_aware
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.schedule import DoctorAvailability, ScheduleSlot
from app.models.user import User
from app.schemas.appointment import AppointmentResponse
from app.schemas.common import Page
from app.schemas.schedule import (
    BookSlotRequest,
    DoctorAvailabilityCreate,
    DoctorAvailabilityResponse,
    GenerateSlotsRequest,
    ScheduleSlotResponse,
)
from app.services.appointment_dedup import reject_if_duplicate_appointment
from app.services.audit import log_action

router = APIRouter(prefix="/schedule", tags=["schedule"])


@router.post("/availability/", response_model=DoctorAvailabilityResponse, status_code=status.HTTP_201_CREATED)
async def create_availability(
    data: DoctorAvailabilityCreate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("appointment:manage")),
):
    if not (await db.execute(select(Doctor).where(Doctor.id == data.doctor_id, Doctor.deleted_at.is_(None)))).scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor not found")

    availability = DoctorAvailability(**data.model_dump())
    db.add(availability)
    await db.flush()
    await log_action(db, actor_id=current_user.id, action="schedule.availability.create", resource_type="doctor_availability", resource_id=availability.id)
    await db.commit()
    await db.refresh(availability)
    return availability


@router.get("/availability/", response_model=Page[DoctorAvailabilityResponse], operation_id="list_availability")
async def list_availability(
    request: Request,
    doctor_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    base_query = select(DoctorAvailability).where(DoctorAvailability.deleted_at.is_(None))
    if doctor_id:
        base_query = base_query.where(DoctorAvailability.doctor_id == doctor_id)

    total = (await db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
    result = await db.execute(base_query.offset(skip).limit(limit))
    return build_page(request, result.scalars().all(), total, skip, limit)


async def _generate_slots(db: AsyncSession, doctor_id: int, days_ahead: int) -> List[ScheduleSlot]:
    availabilities = (
        await db.execute(
            select(DoctorAvailability).where(
                DoctorAvailability.doctor_id == doctor_id, DoctorAvailability.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    if not availabilities:
        return []

    existing_starts = {
        ensure_aware(starts_at)
        for starts_at in (
            await db.execute(select(ScheduleSlot.starts_at).where(ScheduleSlot.doctor_id == doctor_id))
        ).scalars().all()
    }

    now = datetime.now(timezone.utc)
    created: List[ScheduleSlot] = []
    for day_offset in range(1, days_ahead + 1):
        day = (now + timedelta(days=day_offset)).date()
        weekday = day.weekday()

        for availability in availabilities:
            if availability.weekday != weekday:
                continue

            start_h, start_m = (int(part) for part in availability.start_time.split(":"))
            end_h, end_m = (int(part) for part in availability.end_time.split(":"))
            step = timedelta(minutes=availability.slot_minutes or 30)

            current = datetime.combine(day, time(start_h, start_m), tzinfo=timezone.utc)
            window_end = datetime.combine(day, time(end_h, end_m), tzinfo=timezone.utc)

            while current + step <= window_end:
                if current not in existing_starts:
                    slot = ScheduleSlot(
                        doctor_id=doctor_id, starts_at=current, ends_at=current + step, is_booked=False
                    )
                    db.add(slot)
                    created.append(slot)
                    existing_starts.add(current)
                current += step

    await db.flush()
    return created


@router.post("/slots/generate", response_model=List[ScheduleSlotResponse])
async def generate_slots(
    data: GenerateSlotsRequest,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("appointment:manage")),
):
    """Materializes concrete ScheduleSlot rows from a doctor's recurring
    DoctorAvailability windows — idempotent (skips any slot start time that
    already exists), safe to call repeatedly as you extend the booking horizon."""
    if not (await db.execute(select(Doctor).where(Doctor.id == data.doctor_id, Doctor.deleted_at.is_(None)))).scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor not found")

    created = await _generate_slots(db, data.doctor_id, data.days_ahead)
    await log_action(
        db, actor_id=current_user.id, action="schedule.slots.generate",
        resource_type="doctor", resource_id=data.doctor_id, details={"created_count": len(created)},
    )
    await db.commit()
    for slot in created:
        await db.refresh(slot)
    return created


@router.get("/slots/", response_model=Page[ScheduleSlotResponse], operation_id="list_slots")
async def list_slots(
    request: Request,
    doctor_id: Optional[int] = None,
    is_booked: Optional[bool] = None,
    upcoming_only: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """`upcoming_only` exists for the "available slots to book" use case
    (the Scheduling page's recommended-doctor flow) — a slot generated for a
    date that's since passed is still technically `is_booked=False` (nobody
    ever booked it), but offering it as bookable is a dead end/bug in its
    own right, not just a UX nicety; see book_slot's matching guard below.
    Left off by default so the plain "Schedule Slots" table (which staff use
    to review the whole schedule, including past unbooked slots) keeps its
    existing behavior."""
    base_query = select(ScheduleSlot).where(ScheduleSlot.deleted_at.is_(None))
    if doctor_id:
        base_query = base_query.where(ScheduleSlot.doctor_id == doctor_id)
    if is_booked is not None:
        base_query = base_query.where(ScheduleSlot.is_booked == is_booked)
    if upcoming_only:
        base_query = base_query.where(ScheduleSlot.starts_at >= datetime.now(timezone.utc))

    total = (await db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
    result = await db.execute(base_query.order_by(ScheduleSlot.starts_at).offset(skip).limit(limit))
    return build_page(request, result.scalars().all(), total, skip, limit)


@router.post("/slots/{slot_id}/book", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def book_slot(
    slot_id: int,
    data: BookSlotRequest,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    slot = await db.get(ScheduleSlot, slot_id)
    if not slot or slot.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Slot not found")
    if slot.is_booked:
        raise HTTPException(status.HTTP_409_CONFLICT, "Slot already booked")
    # Belt-and-suspenders alongside list_slots's upcoming_only filter: a slot
    # generated for a date that's since passed but never booked is still
    # technically `is_booked=False` — reject booking it outright rather than
    # trusting every caller to have queried with upcoming_only=true first.
    # ensure_aware: SQLite round-trips DateTime(timezone=True) as naive
    # (see app/core/time_utils.py) — comparing it directly against an aware
    # `now()` would raise on SQLite-backed tests while working by accident
    # on Postgres, the exact bug class this helper exists to prevent.
    if ensure_aware(slot.starts_at) < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_409_CONFLICT, "This slot is in the past and can no longer be booked")
    if not (await db.execute(select(Patient).where(Patient.id == data.patient_id, Patient.deleted_at.is_(None)))).scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    await reject_if_duplicate_appointment(
        db, patient_id=data.patient_id, doctor_id=slot.doctor_id, reason=data.reason
    )

    appointment = Appointment(
        patient_id=data.patient_id,
        doctor_id=slot.doctor_id,
        appointment_datetime=slot.starts_at,
        reason=data.reason,
    )
    db.add(appointment)
    await db.flush()

    slot.is_booked = True
    slot.appointment_id = appointment.id

    await log_action(
        db, actor_id=current_user.id, action="schedule.slot.book",
        resource_type="appointment", resource_id=appointment.id, details={"slot_id": slot.id},
    )
    await db.commit()
    await db.refresh(appointment)
    return appointment
