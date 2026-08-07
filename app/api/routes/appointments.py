from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies.auth import get_current_active_user, require_permission
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.models.appointment import Appointment, AppointmentStatusEnum
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.user import User
from app.schemas.appointment import AppointmentCreate, AppointmentResponse, AppointmentUpdate
from app.schemas.common import Page
from app.services.audit import log_action
from app.services.record_scope import _granted_permissions, _own_patient_and_doctor, appointment_visibility_filter

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

    appointment = Appointment(**data.model_dump())
    db.add(appointment)
    await db.flush()
    await log_action(db, actor_id=current_user.id, action="appointment.create", resource_type="appointment", resource_id=appointment.id)
    await db.commit()
    await db.refresh(appointment)
    return appointment


@router.get("/", response_model=Page[AppointmentResponse])
async def get_appointments(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    scope = await appointment_visibility_filter(db, current_user)
    base_query = select(Appointment).where(Appointment.deleted_at.is_(None))
    if scope is not None:
        base_query = base_query.where(scope)
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
