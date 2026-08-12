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
from app.services.record_scope import (
    _granted_permissions,
    _own_patient_and_doctor,
    medical_record_visibility_filter,
)

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

    # medical_record:manage alone used to let anyone holding it create a
    # record for *any* patient/doctor pair — the same ownership gap fixed
    # below for update/delete. A caller with view_all (care_coordinator/
    # doctor/admin) stays unrestricted (existing broad-access design, same
    # trade-off as patient_visibility_filter elsewhere); a view_own-only
    # caller (patient/pcp/specialist) must actually be a party to the record
    # they're creating — patients write their own chart (e.g. self-reported
    # lab results), pcp/specialist write encounters under their own name.
    granted = await _granted_permissions(db, current_user)
    if "medical_record:view_all" not in granted and "admin:*" not in granted:
        patient, doctor = await _own_patient_and_doctor(db, current_user)
        if patient is not None:
            if data.patient_id != patient.id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only add medical records to your own chart")
        elif doctor is not None:
            if data.doctor_id != doctor.id:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only author medical records under your own doctor account")
        else:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account isn't linked to a patient or doctor record")

    record = MedicalRecord(**data.model_dump())
    db.add(record)
    await db.flush()
    await log_action(db, actor_id=current_user.id, action="medical_record.create", resource_type="medical_record", resource_id=record.id)
    await db.commit()
    await db.refresh(record)
    return record


@router.get("/", response_model=Page[MedicalRecordResponse], operation_id="list_medical_records")
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
    # Was a raw unscoped lookup — any medical_record:manage holder could
    # edit *any* patient's record regardless of whether they were a party to
    # it. Now the same visibility scope the GET routes already use: patient
    # only their own, pcp/specialist only records they're the treating
    # doctor on, care_coordinator/doctor/admin (view_all) unrestricted.
    record = await _get_scoped_medical_record(db, current_user, record_id)

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
    # Same ownership-scoping fix as update_medical_record above.
    record = await _get_scoped_medical_record(db, current_user, record_id)

    record.deleted_at = datetime.now(timezone.utc)
    await log_action(db, actor_id=current_user.id, action="medical_record.delete", resource_type="medical_record", resource_id=record.id)
    await db.commit()
    return {"message": "Medical record deleted successfully"}
