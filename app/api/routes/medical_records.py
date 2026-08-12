from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies.auth import get_current_active_user, require_permission
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.models.doctor import Doctor
from app.models.medical_record import MedicalRecord, MedicalRecordDocument
from app.models.patient import Patient
from app.models.user import User
from app.schemas.common import Page
from app.schemas.medical_record import (
    MedicalRecordCreate,
    MedicalRecordDocumentResponse,
    MedicalRecordResponse,
    MedicalRecordUpdate,
)
from app.services.audit import log_action
from app.services.document_access import user_can_view_medical_record
from app.services.record_scope import (
    _granted_permissions,
    _own_patient_and_doctor,
    medical_record_visibility_filter,
)
from app.services.storage import save_medical_record_document

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

    if data.doctor_id is not None:
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


@router.post(
    "/quick-upload", response_model=MedicalRecordDocumentResponse, status_code=status.HTTP_201_CREATED
)
async def quick_upload_medical_record_document(
    patient_id: int = Form(...),
    record_type: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("medical_record:manage")),
):
    """One-call "just upload a document" path — creates a new, doctor-less
    MedicalRecord and attaches the file to it in a single request, for a
    patient uploading straight into their own chart with no existing record
    to pick and no treating doctor involved yet. Deliberately narrower than
    the generic POST /medical-records/: self-service only (a caller without
    medical_record:view_all must be uploading for their own linked patient
    record) — a pcp/specialist authoring a real encounter should use
    POST /medical-records/ (with their own doctor_id) instead."""
    if not (await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )).scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    granted = await _granted_permissions(db, current_user)
    if "medical_record:view_all" not in granted and "admin:*" not in granted:
        patient, _doctor = await _own_patient_and_doctor(db, current_user)
        if patient is None or patient_id != patient.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only upload documents to your own chart")

    record = MedicalRecord(
        patient_id=patient_id,
        doctor_id=None,
        visit_date=datetime.now(timezone.utc),
        record_type=record_type or "patient_upload",
        notes=notes,
    )
    db.add(record)
    await db.flush()

    storage_path = await save_medical_record_document(record.id, file)
    document = MedicalRecordDocument(
        medical_record_id=record.id, filename=file.filename or "upload", storage_path=storage_path,
    )
    db.add(document)
    await db.flush()

    await log_action(
        db, actor_id=current_user.id, action="medical_record.document.upload",
        resource_type="medical_record_document", resource_id=document.id,
        details={"medical_record_id": record.id, "filename": document.filename},
    )
    await db.commit()
    await db.refresh(document)
    return document


@router.get("/", response_model=Page[MedicalRecordResponse], operation_id="list_medical_records")
async def get_medical_records(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    # int, not Optional[int]: fastapi_mcp injects a contradictory top-level
    # `type` onto any Optional query param's schema (see the detailed
    # comment on list_referrals's `q` param) — 0 is never a real patient id,
    # so this is behavior-preserving for the existing `if patient_id:` check.
    patient_id: int = 0,
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


@router.get("/{record_id}/documents", response_model=List[MedicalRecordDocumentResponse])
async def list_medical_record_documents(
    record_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    record = await _get_scoped_medical_record(db, current_user, record_id)
    result = await db.execute(
        select(MedicalRecordDocument)
        .where(MedicalRecordDocument.medical_record_id == record.id)
        .order_by(MedicalRecordDocument.created_at)
    )
    return result.scalars().all()


@router.get("/documents/{document_id}/download")
async def download_medical_record_document(
    document_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Deliberately NOT scoped via `_get_scoped_medical_record` (which only
    grants access by the record's own patient_id/doctor_id) — a specialist
    or pcp who can see a referral/appointment should be able to inspect the
    documents attached to it even when they aren't that record's own
    `doctor_id`. See app.services.document_access.user_can_view_medical_record
    for the full set of paths that can grant access."""
    document = await db.get(MedicalRecordDocument, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if not await user_can_view_medical_record(db, current_user, document.medical_record_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    path = Path(document.storage_path)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found on disk")
    return FileResponse(path, filename=document.filename)


@router.post(
    "/{record_id}/documents", response_model=MedicalRecordDocumentResponse, status_code=status.HTTP_201_CREATED
)
async def upload_medical_record_document(
    record_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("medical_record:manage")),
):
    """Attaches a document to an already-selected/created record — the
    counterpart to POST /medical-records/quick-upload for the "I already
    have a record, add another file to it" case."""
    record = await _get_scoped_medical_record(db, current_user, record_id)

    storage_path = await save_medical_record_document(record.id, file)
    document = MedicalRecordDocument(
        medical_record_id=record.id, filename=file.filename or "upload", storage_path=storage_path,
    )
    db.add(document)
    await db.flush()
    await log_action(
        db, actor_id=current_user.id, action="medical_record.document.upload",
        resource_type="medical_record_document", resource_id=document.id,
        details={"medical_record_id": record.id, "filename": document.filename},
    )
    await db.commit()
    await db.refresh(document)
    return document


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
