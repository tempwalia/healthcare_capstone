from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies.auth import get_current_active_user, require_permission
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.models.appointment import Appointment, AppointmentStatusEnum
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.referral import ReferralOutcome
from app.models.user import User
from app.schemas.appointment import AppointmentCreate, AppointmentResponse, AppointmentUpdate
from app.schemas.common import Page
from app.schemas.referral import ReferralOutcomeCreate, ReferralOutcomeResponse
from app.services.appointment_dedup import reject_if_duplicate_appointment
from app.services.audit import log_action
from app.services.notifications import create_notification
from app.services.record_scope import _granted_permissions, _own_patient_and_doctor, appointment_visibility_filter
from app.services.referral_outcome import generate_completion_summary

router = APIRouter(prefix="/appointments", tags=["appointments"])


async def _get_scoped_appointment(db: AsyncSession, current_user: User, appointment_id: int) -> Appointment:
    scope = await appointment_visibility_filter(db, current_user)
    query = (
        select(Appointment)
        .options(selectinload(Appointment.patient), selectinload(Appointment.doctor))
        .where(Appointment.id == appointment_id, Appointment.deleted_at.is_(None))
    )
    if scope is not None:
        query = query.where(scope)

    appointment = (await db.execute(query)).scalar_one_or_none()
    if not appointment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    return appointment


@router.post("/", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    data: AppointmentCreate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("appointment:manage")),
):
    patient_ok = (await db.execute(
        select(Patient).where(Patient.id == data.patient_id, Patient.deleted_at.is_(None))
    )).scalar_one_or_none()
    if not patient_ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    doctor_ok = (await db.execute(
        select(Doctor).where(Doctor.id == data.doctor_id, Doctor.deleted_at.is_(None))
    )).scalar_one_or_none()
    if not doctor_ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor not found")

    await reject_if_duplicate_appointment(
        db, patient_id=data.patient_id, doctor_id=data.doctor_id, reason=data.reason
    )

    appointment = Appointment(**data.model_dump())
    db.add(appointment)
    await db.flush()
    await log_action(db, actor_id=current_user.id, action="appointment.create", resource_type="appointment", resource_id=appointment.id)
    await db.commit()
    await db.refresh(appointment)
    return appointment


@router.get("/", response_model=Page[AppointmentResponse], operation_id="list_appointments")
async def get_appointments(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    doctor_id: Optional[int] = None,
    upcoming_only: Optional[bool] = None,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """`doctor_id`/`upcoming_only` narrow the query for a "My Day" style view
    — additive on top of the existing appointment_visibility_filter scope,
    not a change to it. A provider still technically holds appointment:view_all
    (an existing, intentional "any provider may need to cover" trade-off);
    this just gives the frontend a precise query instead of forcing it to
    filter the whole platform's appointments client-side."""
    scope = await appointment_visibility_filter(db, current_user)
    base_query = select(Appointment).where(Appointment.deleted_at.is_(None))
    if scope is not None:
        base_query = base_query.where(scope)
    if doctor_id is not None:
        base_query = base_query.where(Appointment.doctor_id == doctor_id)
    if upcoming_only:
        base_query = base_query.where(Appointment.appointment_datetime >= datetime.now(timezone.utc))
        # Soonest first — the "My Day"/"upcoming appointment card" use cases
        # this filter exists for both want the next appointment, not
        # whatever the DB's default insertion order happens to return.
        base_query = base_query.order_by(Appointment.appointment_datetime.asc())
    total = (await db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
    result = await db.execute(
        base_query.options(selectinload(Appointment.patient), selectinload(Appointment.doctor))
        .offset(skip)
        .limit(limit)
    )
    return build_page(request, result.scalars().all(), total, skip, limit)


@router.get("/{appointment_id}", response_model=AppointmentResponse)
async def get_appointment(
    appointment_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    return await _get_scoped_appointment(db, current_user, appointment_id)


@router.put("/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(
    appointment_id: int,
    data: AppointmentUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Full edit access requires `appointment:manage` (pcp/specialist/
    care_coordinator/admin). A caller who only holds `appointment:view_own`
    (the `patient` role) may still reschedule or cancel an appointment that
    is theirs — a patient couldn't act on their own booking at all
    otherwise, since `appointment:manage` is deliberately staff-only."""
    appointment = await _get_scoped_appointment(db, current_user, appointment_id)
    granted = await _granted_permissions(db, current_user)

    if "appointment:manage" not in granted and "admin:*" not in granted:
        patient, _ = await _own_patient_and_doctor(db, current_user)
        if not patient or appointment.patient_id != patient.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing required permission: appointment:manage")

        submitted_fields = set(data.model_dump(exclude_unset=True).keys())
        allowed_fields = {"appointment_datetime", "status", "reason"}
        if submitted_fields - allowed_fields:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Patients may only reschedule (appointment_datetime) or cancel (status) their own appointment",
            )
        if data.status is not None and data.status != AppointmentStatusEnum.CANCELLED:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Patients may only cancel their own appointment, not set other statuses"
            )

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(appointment, field, value)

    await log_action(db, actor_id=current_user.id, action="appointment.update", resource_type="appointment", resource_id=appointment.id)
    await db.commit()
    await db.refresh(appointment)
    return appointment


@router.delete("/{appointment_id}")
async def delete_appointment(
    appointment_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("appointment:manage")),
):
    result = await db.execute(
        select(Appointment).where(Appointment.id == appointment_id, Appointment.deleted_at.is_(None))
    )
    appointment = result.scalar_one_or_none()
    if not appointment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")

    appointment.deleted_at = datetime.now(timezone.utc)
    await log_action(db, actor_id=current_user.id, action="appointment.delete", resource_type="appointment", resource_id=appointment.id)
    await db.commit()
    return {"message": "Appointment deleted successfully"}


@router.post(
    "/{appointment_id}/outcome", response_model=ReferralOutcomeResponse, status_code=status.HTTP_202_ACCEPTED,
)
async def record_appointment_outcome(
    appointment_id: int,
    data: ReferralOutcomeCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("referral:record_outcome")),
):
    """The direct-booked-appointment counterpart to `POST /referral/requests/
    {id}/outcome` — previously the *only* way to record a consultation and
    generate a completion summary was through a referral; an appointment
    booked straight off an open slot (no referral involved, referral_id
    stays NULL) had no equivalent at all. Reuses the exact same
    `ReferralOutcome` row shape, `referral:record_outcome` permission, and
    `generate_completion_summary` background job — see
    app/models/referral.py's `ReferralOutcome` docstring for why exactly one
    of referral_request_id/appointment_id is set. Scoped via
    _get_scoped_appointment (not a raw lookup) for the same reason the
    referral route uses _get_scoped_referral: a view_own-scoped specialist
    must actually be the assigned doctor on this appointment, not just hold
    the permission in the abstract."""
    appointment = await _get_scoped_appointment(db, current_user, appointment_id)

    existing = (
        await db.execute(select(ReferralOutcome).where(ReferralOutcome.appointment_id == appointment_id))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "An outcome has already been recorded for this appointment")

    outcome = ReferralOutcome(
        appointment_id=appointment.id, recorded_by_user_id=current_user.id, **data.model_dump()
    )
    db.add(outcome)
    appointment.status = AppointmentStatusEnum.COMPLETED

    await log_action(
        db, actor_id=current_user.id, action="appointment.outcome.record",
        resource_type="appointment", resource_id=appointment.id,
    )

    patient = await db.get(Patient, appointment.patient_id)
    if patient is not None and patient.user_id is not None:
        await create_notification(
            db, user_id=patient.user_id, title="Your consultation summary is ready",
            body=f"A consult outcome has been recorded for your appointment #{appointment.id}.",
        )
    else:
        # Same best-effort-skip-with-audit-log convention as the referral
        # outcome route's equivalent skip.
        await log_action(
            db, actor_id=None, action="referral.notification.skipped",
            resource_type="appointment", resource_id=appointment.id,
            details={"reason": "patient has no linked user account", "event": "outcome_recorded"},
        )

    await db.commit()
    await db.refresh(outcome)

    background_tasks.add_task(generate_completion_summary, outcome.id)
    return outcome


@router.get("/{appointment_id}/outcome", response_model=ReferralOutcomeResponse)
async def get_appointment_outcome(
    appointment_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Same visibility as the appointment itself — the patient it belongs
    to, the assigned doctor, or staff with appointment:view_all can read
    the resulting summary once one exists. Recording (POST, above) stays
    staff-only via referral:record_outcome; this is read-only."""
    await _get_scoped_appointment(db, current_user, appointment_id)
    outcome = (
        await db.execute(select(ReferralOutcome).where(ReferralOutcome.appointment_id == appointment_id))
    ).scalar_one_or_none()
    if not outcome:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No outcome recorded for this appointment")
    return outcome
