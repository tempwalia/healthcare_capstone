from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies.auth import get_current_active_user
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.user import User
from app.schemas.appointment import AppointmentCreate, AppointmentResponse, AppointmentUpdate
from app.schemas.common import Page
from app.services.audit import log_action

router = APIRouter(prefix="/appointments", tags=["appointments"])


@router.post("/", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    data: AppointmentCreate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
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
    base_query = select(Appointment).where(Appointment.deleted_at.is_(None))
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
    result = await db.execute(
        select(Appointment)
        .options(selectinload(Appointment.patient), selectinload(Appointment.doctor))
        .where(Appointment.id == appointment_id, Appointment.deleted_at.is_(None))
    )
    appointment = result.scalar_one_or_none()
    if not appointment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    return appointment


@router.put("/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(
    appointment_id: int,
    data: AppointmentUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Appointment).where(Appointment.id == appointment_id, Appointment.deleted_at.is_(None))
    )
    appointment = result.scalar_one_or_none()
    if not appointment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")

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
    current_user: User = Depends(get_current_active_user),
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
