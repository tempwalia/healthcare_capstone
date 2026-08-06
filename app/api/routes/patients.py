from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_active_user
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.models.patient import Patient
from app.models.user import User
from app.schemas.common import Page
from app.schemas.patient import PatientCreate, PatientResponse, PatientUpdate
from app.services.audit import log_action

router = APIRouter(prefix="/patients", tags=["patients"])


@router.post("/", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
async def create_patient(
    data: PatientCreate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    patient = Patient(**data.model_dump())
    db.add(patient)
    await db.flush()
    await log_action(db, actor_id=current_user.id, action="patient.create", resource_type="patient", resource_id=patient.id)
    await db.commit()
    await db.refresh(patient)
    return patient


@router.get("/", response_model=Page[PatientResponse])
async def get_patients(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    base_query = select(Patient).where(Patient.deleted_at.is_(None))
    total = (await db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
    result = await db.execute(base_query.offset(skip).limit(limit))
    return build_page(request, result.scalars().all(), total, skip, limit)


@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(
    patient_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return patient


@router.put("/{patient_id}", response_model=PatientResponse)
async def update_patient(
    patient_id: int,
    data: PatientUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)

    await log_action(db, actor_id=current_user.id, action="patient.update", resource_type="patient", resource_id=patient.id)
    await db.commit()
    await db.refresh(patient)
    return patient


@router.delete("/{patient_id}")
async def delete_patient(
    patient_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    patient.deleted_at = datetime.now(timezone.utc)
    await log_action(db, actor_id=current_user.id, action="patient.delete", resource_type="patient", resource_id=patient.id)
    await db.commit()
    return {"message": "Patient deleted successfully"}
