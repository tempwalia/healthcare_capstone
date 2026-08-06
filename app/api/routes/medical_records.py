from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies.auth import get_current_active_user, require_permission
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.models.doctor import Doctor
from app.models.medical_record import MedicalRecord
from app.models.patient import Patient
from app.models.user import User
from app.schemas.common import Page
from app.schemas.medical_record import (
    MedicalRecordCreate,
    MedicalRecordResponse,
    MedicalRecordUpdate,
)
from app.services.audit import log_action
from app.services.record_scope import medical_record_visibility_filter

router = APIRouter(prefix="/medical-records", tags=["medical-records"])


async def _get_scoped_medical_record(db: AsyncSession, current_user: User, record_id: int) -> MedicalRecord:
    scope = await medical_record_visibility_filter(db, current_user)
    query = (
        select(MedicalRecord)
        .options(selectinload(MedicalRecord.patient), selectinload(MedicalRecord.doctor))
        .where(MedicalRecord.id == record_id, MedicalRecord.deleted_at.is_(None))
    )
    if scope is not None:
        query = query.where(scope)

    record = (await db.execute(query)).scalar_one_or_none()
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Medical record not found")
    return record


@router.post("/", response_model=MedicalRecordResponse, status_code=status.HTTP_201_CREATED)
async def create_medical_record(
    data: MedicalRecordCreate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("medical_record:manage")),
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

    record = MedicalRecord(**data.model_dump())
    db.add(record)
    await db.flush()
    await log_action(db, actor_id=current_user.id, action="medical_record.create", resource_type="medical_record", resource_id=record.id)
    await db.commit()
    await db.refresh(record)
    return record


@router.get("/", response_model=Page[MedicalRecordResponse])
async def get_medical_records(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    patient_id: Optional[int] = None,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    scope = await medical_record_visibility_filter(db, current_user)
    base_query = select(MedicalRecord).where(MedicalRecord.deleted_at.is_(None))
    if patient_id:
        base_query = base_query.where(MedicalRecord.patient_id == patient_id)
    if scope is not None:
        base_query = base_query.where(scope)

    total = (await db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
    result = await db.execute(
        base_query.options(selectinload(MedicalRecord.patient), selectinload(MedicalRecord.doctor))
        .offset(skip)
        .limit(limit)
    )
    return build_page(request, result.scalars().all(), total, skip, limit)


@router.get("/{record_id}", response_model=MedicalRecordResponse)
async def get_medical_record(
    record_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    return await _get_scoped_medical_record(db, current_user, record_id)


@router.put("/{record_id}", response_model=MedicalRecordResponse)
async def update_medical_record(
    record_id: int,
    data: MedicalRecordUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("medical_record:manage")),
):
    result = await db.execute(
        select(MedicalRecord).where(MedicalRecord.id == record_id, MedicalRecord.deleted_at.is_(None))
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Medical record not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(record, field, value)

    await log_action(db, actor_id=current_user.id, action="medical_record.update", resource_type="medical_record", resource_id=record.id)
    await db.commit()
    await db.refresh(record)
    return record


@router.delete("/{record_id}")
async def delete_medical_record(
    record_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("medical_record:manage")),
):
    result = await db.execute(
        select(MedicalRecord).where(MedicalRecord.id == record_id, MedicalRecord.deleted_at.is_(None))
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Medical record not found")

    record.deleted_at = datetime.now(timezone.utc)
    await log_action(db, actor_id=current_user.id, action="medical_record.delete", resource_type="medical_record", resource_id=record.id)
    await db.commit()
    return {"message": "Medical record deleted successfully"}
