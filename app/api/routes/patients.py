from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_active_user, require_permission
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.models.patient import Patient
from app.models.user import User
from app.schemas.common import Page
from app.schemas.patient import PatientContextResponse, PatientCreate, PatientResponse, PatientUpdate
from app.services.audit import log_action
from app.services.insurance import random_policy
from app.services.patient_context import gather_patient_context
from app.services.record_scope import patient_visibility_filter

router = APIRouter(prefix="/patients", tags=["patients"])


async def _get_scoped_patient(db: AsyncSession, current_user: User, patient_id: int) -> Patient:
    scope = await patient_visibility_filter(db, current_user)
    query = select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    if scope is not None:
        query = query.where(scope)

    patient = (await db.execute(query)).scalar_one_or_none()
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return patient


@router.post("/", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
async def create_patient(
    data: PatientCreate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("patient:manage")),
):
    patient_data = data.model_dump()
    # POC convenience: don't make insurance entry a manual step before a
    # patient can be referred anywhere — a caller who left both fields blank
    # gets a randomly assigned policy/payer plan (mirrors
    # scripts/seed_sample_insurance.py's weighting), so eligibility checking
    # always has something real to verify against. A caller who did supply
    # their own values is left untouched.
    if not patient_data.get("insurance_provider") and not patient_data.get("insurance_policy_number"):
        policy_number, provider = random_policy()
        patient_data["insurance_policy_number"] = policy_number
        patient_data["insurance_provider"] = provider

    patient = Patient(**patient_data)
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
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """`q` free-text searches name/email/phone within whatever this caller's
    visibility scope already allows — previously the dashboard's search box
    only filtered the current page client-side (a documented simplification,
    see static/js/resource.js), which meant a patient outside the first page
    was simply unfindable. Now a real, server-side, scope-respecting search."""
    scope = await patient_visibility_filter(db, current_user)
    base_query = select(Patient).where(Patient.deleted_at.is_(None))
    if scope is not None:
        base_query = base_query.where(scope)
    if q and q.strip():
        term = f"%{q.strip()}%"
        base_query = base_query.where(
            or_(
                Patient.first_name.ilike(term),
                Patient.last_name.ilike(term),
                Patient.email.ilike(term),
                Patient.phone.ilike(term),
            )
        )
    total = (await db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
    result = await db.execute(base_query.offset(skip).limit(limit))
    return build_page(request, result.scalars().all(), total, skip, limit)


@router.get("/me", response_model=PatientResponse, operation_id="get_my_patient")
async def get_my_patient_record(
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Resolves "which Patient row is mine" — the self-service counterpart to
    GET /doctors/me. Registered before /{patient_id} so "me" is never parsed
    as an int patient_id. Exists mainly so the assistant (and the dashboard)
    never has to guess or be told a patient_id for "my own" questions."""
    result = await db.execute(
        select(Patient).where(Patient.user_id == current_user.id, Patient.deleted_at.is_(None))
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No patient record linked to your account")
    return patient


@router.get("/me/context", response_model=PatientContextResponse, operation_id="get_my_patient_context")
async def get_my_patient_context(
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Same aggregation as GET /{patient_id}/context, self-scoped with no id
    parameter at all — the assistant tool a `patient`-role caller gets
    (app/agents/assistant_graph.py), instead of get_patient_context, so
    there's never an id for the model to guess, mistype, or be tricked into
    substituting. Registered before /{patient_id} for the same reason as
    /me above."""
    result = await db.execute(
        select(Patient).where(Patient.user_id == current_user.id, Patient.deleted_at.is_(None))
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No patient record linked to your account yet")
    return await gather_patient_context(db, current_user, patient.id)


@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(
    patient_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    return await _get_scoped_patient(db, current_user, patient_id)


@router.get("/{patient_id}/context", response_model=PatientContextResponse, operation_id="get_patient_context")
async def get_patient_context(
    patient_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Aggregates everything about this patient the caller is already allowed
    to see (appointments, medical records, referrals, insurance, derived care
    team) into one payload — the platform's answer to "once a patient is
    created it should already be available to all [roles that have a reason
    to see it]" instead of everyone hand-navigating four separate pages. Never
    broader than assembling the same four GETs by hand: every sub-list reuses
    that resource's own visibility filter. The staff-facing assistant tool
    (pcp/specialist/care_coordinator/doctor/admin) — patient-role callers get
    get_my_patient_context instead, which needs no id at all."""
    return await gather_patient_context(db, current_user, patient_id)


@router.put("/{patient_id}", response_model=PatientResponse)
async def update_patient(
    patient_id: int,
    data: PatientUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("patient:manage")),
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
    current_user: User = Depends(require_permission("patient:manage")),
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
