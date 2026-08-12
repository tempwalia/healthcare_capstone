from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_active_user, require_permission
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.models.doctor import Doctor
from app.models.medical_record import MedicalRecord
from app.models.user import User
from app.schemas.common import Page
from app.schemas.doctor import DoctorCreate, DoctorResponse, DoctorUpdate
from app.services.audit import log_action
from app.services.doctor_recommendation import infer_specialty, recommend_platform_doctors

router = APIRouter(prefix="/doctors", tags=["doctors"])

# GET routes deliberately stay open to any authenticated user: the doctor
# directory (name, specialization, department, bio — no PHI) is meant to be
# browsable by patients choosing a specialist and staff alike, same as
# `GET /doctors/search`. Only mutations are ownership-gated, via
# `doctor:manage` (roster administration — care_coordinator/admin/pcp/
# specialist; not the bare `patient` role).


@router.post("/", response_model=DoctorResponse, status_code=status.HTTP_201_CREATED)
async def create_doctor(
    data: DoctorCreate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("doctor:manage")),
):
    doctor = Doctor(**data.model_dump())
    db.add(doctor)
    await db.flush()
    await log_action(db, actor_id=current_user.id, action="doctor.create", resource_type="doctor", resource_id=doctor.id)
    await db.commit()
    await db.refresh(doctor)
    return doctor


@router.get("/", response_model=Page[DoctorResponse])
async def get_doctors(
    request: Request,
    q: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """`q` powers the "search any doctor by name" fallback — irrespective of
    specialty or city — for when the recommendation step doesn't surface
    who the user is looking for."""
    base_query = select(Doctor).where(Doctor.deleted_at.is_(None))
    if q:
        base_query = base_query.where(
            or_(Doctor.first_name.ilike(f"%{q}%"), Doctor.last_name.ilike(f"%{q}%"))
        )
    total = (await db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
    result = await db.execute(base_query.offset(skip).limit(limit))
    return build_page(request, result.scalars().all(), total, skip, limit)


@router.get("/me", response_model=DoctorResponse)
async def get_my_doctor_record(
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Resolves "which Doctor row is mine" for a pcp/specialist account — the
    one thing nothing in the API surfaced before (DoctorResponse has no
    user_id). Registered before /{doctor_id} so "me" is never parsed as an
    int doctor_id."""
    result = await db.execute(
        select(Doctor).where(Doctor.user_id == current_user.id, Doctor.deleted_at.is_(None))
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No doctor record linked to your account")
    return doctor


@router.get("/recommend")
async def recommend_doctors(
    reason: str = "",
    specialty: Optional[str] = None,
    patient_id: Optional[int] = None,
    medical_record_id: Optional[int] = None,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """"Suggest a doctor for me" for direct appointment booking — the same
    specialty-inference + ranking logic the referral workflow's specialist
    recommendation step already uses (see
    app.services.doctor_recommendation), just sourced from our own bookable
    `doctors` table instead of the external mock provider directory, since
    a direct booking needs a real doctor with real schedule_slots, not a
    synthetic external candidate. Registered before /{doctor_id} so
    "recommend" is never parsed as an int doctor_id. Optional and additive —
    every existing manual doctor-picker flow is unchanged.

    `medical_record_id`, when given, folds that record's symptoms/diagnosis
    text into specialty inference alongside (or instead of) typed `reason` —
    the unified "New Request" flow lets a user pick an existing record
    instead of retyping their symptoms."""
    reason_parts = [reason] if reason else []
    if medical_record_id is not None:
        record = await db.get(MedicalRecord, medical_record_id)
        if record is not None and record.deleted_at is None:
            if record.symptoms:
                reason_parts.append(record.symptoms)
            if record.diagnosis:
                reason_parts.append(record.diagnosis)
    combined_reason = " ".join(reason_parts)

    resolved_specialty = specialty or infer_specialty([], combined_reason)
    return await recommend_platform_doctors(db, specialty=resolved_specialty, patient_id=patient_id)


@router.get("/{doctor_id}", response_model=DoctorResponse)
async def get_doctor(
    doctor_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Doctor).where(Doctor.id == doctor_id, Doctor.deleted_at.is_(None))
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor not found")
    return doctor


@router.put("/{doctor_id}", response_model=DoctorResponse)
async def update_doctor(
    doctor_id: int,
    data: DoctorUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("doctor:manage")),
):
    result = await db.execute(
        select(Doctor).where(Doctor.id == doctor_id, Doctor.deleted_at.is_(None))
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(doctor, field, value)

    await log_action(db, actor_id=current_user.id, action="doctor.update", resource_type="doctor", resource_id=doctor.id)
    await db.commit()
    await db.refresh(doctor)
    return doctor


@router.delete("/{doctor_id}")
async def delete_doctor(
    doctor_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("doctor:manage")),
):
    result = await db.execute(
        select(Doctor).where(Doctor.id == doctor_id, Doctor.deleted_at.is_(None))
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor not found")

    doctor.deleted_at = datetime.now(timezone.utc)
    await log_action(db, actor_id=current_user.id, action="doctor.delete", resource_type="doctor", resource_id=doctor.id)
    await db.commit()
    return {"message": "Doctor deleted successfully"}
