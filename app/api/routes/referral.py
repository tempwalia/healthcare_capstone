from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies.auth import get_current_active_user, require_permission
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.events import broadcaster
from app.events.outbox import write_outbox_event
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.referral import (
    ReferralDocument,
    ReferralOutcome,
    ReferralRequest,
    ReferralWorkflowStatus,
    SpecialistNote,
)
from app.models.role import Role
from app.models.user import User
from app.schemas.common import Page
from app.schemas.referral import (
    ReferralDocumentResponse,
    ReferralOutcomeCreate,
    ReferralOutcomeResponse,
    ReferralRequestCreate,
    ReferralRequestResponse,
    ReferralRequestUpdate,
    SpecialistNoteResponse,
)
from app.services.audit import log_action
from app.services.referral_outcome import generate_completion_summary
from app.services.referral_scope import referral_visibility_filter
from app.services.referral_workflow import run_referral_workflow
from app.services.storage import save_referral_document

router = APIRouter(prefix="/referral", tags=["referral"])


async def _get_scoped_referral(db: AsyncSession, current_user: User, referral_id: int) -> ReferralRequest:
    scope = await referral_visibility_filter(db, current_user)
    query = select(ReferralRequest).where(
        ReferralRequest.id == referral_id, ReferralRequest.deleted_at.is_(None)
    )
    if scope is not None:
        query = query.where(scope)

    referral = (await db.execute(query)).scalar_one_or_none()
    if not referral:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Referral not found")
    return referral


async def _get_staff_scoped_referral(db: AsyncSession, current_user: User, referral_id: int) -> ReferralRequest:
    """Same lookup as `_get_scoped_referral` but deliberately excludes the
    patient-visibility branch — the consult outcome/interaction summary
    stays staff-only even though a patient can otherwise see the referral
    itself via `referral:view_own`."""
    result = await db.execute(
        select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.id == current_user.id)
    )
    user = result.scalar_one()
    granted = {p.name for role in user.roles for p in role.permissions}

    query = select(ReferralRequest).where(ReferralRequest.id == referral_id, ReferralRequest.deleted_at.is_(None))
    if "referral:view_all" not in granted and "admin:*" not in granted:
        doctor = (await db.execute(select(Doctor).where(Doctor.user_id == current_user.id))).scalar_one_or_none()
        if doctor is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not authorized to view this referral's outcome")
        query = query.where(ReferralRequest.referring_doctor_id == doctor.id)

    referral = (await db.execute(query)).scalar_one_or_none()
    if not referral:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Referral not found")
    return referral


@router.post("/requests/", response_model=ReferralRequestResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_referral(
    data: ReferralRequestCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("referral:create")),
):
    if not (await db.execute(select(Patient).where(Patient.id == data.patient_id, Patient.deleted_at.is_(None)))).scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    if not (await db.execute(select(Doctor).where(Doctor.id == data.referring_doctor_id, Doctor.deleted_at.is_(None)))).scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Referring doctor not found")
    if data.specialist_id is not None and not (
        await db.execute(select(Doctor).where(Doctor.id == data.specialist_id, Doctor.deleted_at.is_(None)))
    ).scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Specialist not found")

    referral = ReferralRequest(**data.model_dump(), status=ReferralWorkflowStatus.SUBMITTED.value)
    db.add(referral)
    await db.flush()
    referral.workflow_thread_id = f"referral-{referral.id}"

    await log_action(db, actor_id=current_user.id, action="referral.submit", resource_type="referral_request", resource_id=referral.id)
    await write_outbox_event(
        db, "referral.submitted", {"referral_id": referral.id, "status": referral.status}, referral_id=referral.id
    )
    await db.commit()
    await db.refresh(referral)

    background_tasks.add_task(run_referral_workflow, referral.id)
    return referral


@router.get("/requests/", response_model=Page[ReferralRequestResponse], operation_id="list_referrals")
async def list_referrals(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    status_filter: Optional[str] = None,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    scope = await referral_visibility_filter(db, current_user)
    base_query = select(ReferralRequest).where(ReferralRequest.deleted_at.is_(None))
    if scope is not None:
        base_query = base_query.where(scope)
    if status_filter:
        base_query = base_query.where(ReferralRequest.status == status_filter)

    total = (await db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
    result = await db.execute(base_query.order_by(ReferralRequest.id.desc()).offset(skip).limit(limit))
    return build_page(request, result.scalars().all(), total, skip, limit)


@router.get("/requests/{referral_id}", response_model=ReferralRequestResponse, operation_id="get_referral")
async def get_referral(
    referral_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    return await _get_scoped_referral(db, current_user, referral_id)


@router.patch("/requests/{referral_id}", response_model=ReferralRequestResponse)
async def update_referral(
    referral_id: int,
    data: ReferralRequestUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    referral = await _get_scoped_referral(db, current_user, referral_id)

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(referral, field, value)

    await log_action(db, actor_id=current_user.id, action="referral.update", resource_type="referral_request", resource_id=referral.id)
    await db.commit()
    await db.refresh(referral)
    return referral


@router.delete("/requests/{referral_id}")
async def delete_referral(
    referral_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    referral = await _get_scoped_referral(db, current_user, referral_id)
    referral.deleted_at = datetime.now(timezone.utc)
    await log_action(db, actor_id=current_user.id, action="referral.delete", resource_type="referral_request", resource_id=referral.id)
    await db.commit()
    return {"message": "Referral deleted successfully"}


@router.post(
    "/requests/{referral_id}/documents",
    response_model=ReferralDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_referral_document(
    referral_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    referral = await _get_scoped_referral(db, current_user, referral_id)

    storage_path = await save_referral_document(referral.id, file)
    document = ReferralDocument(
        referral_request_id=referral.id,
        filename=file.filename or "upload",
        storage_path=storage_path,
        extraction_status="queued",
    )
    db.add(document)
    await db.flush()
    await log_action(
        db, actor_id=current_user.id, action="referral.document.upload",
        resource_type="referral_document", resource_id=document.id,
        details={"referral_id": referral.id, "filename": document.filename},
    )
    await db.commit()
    await db.refresh(document)

    # The workflow graph reached `await_documents` -> END the instant this
    # referral was submitted (before any document could possibly exist yet)
    # — re-running it re-enters `intake_node`, which re-checks documents from
    # scratch and proceeds onward once the required types are all present.
    if referral.status == ReferralWorkflowStatus.AWAITING_DOCUMENTS.value:
        background_tasks.add_task(run_referral_workflow, referral.id)

    return document


@router.get(
    "/requests/{referral_id}/documents",
    response_model=List[ReferralDocumentResponse],
    operation_id="list_referral_documents",
)
async def list_referral_documents(
    referral_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    referral = await _get_scoped_referral(db, current_user, referral_id)
    result = await db.execute(
        select(ReferralDocument).where(ReferralDocument.referral_request_id == referral.id)
    )
    return result.scalars().all()


@router.get(
    "/requests/{referral_id}/notes",
    response_model=List[SpecialistNoteResponse],
    operation_id="list_specialist_notes",
)
async def list_specialist_notes(
    referral_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    referral = await _get_scoped_referral(db, current_user, referral_id)
    result = await db.execute(
        select(SpecialistNote).where(SpecialistNote.referral_request_id == referral.id)
    )
    return result.scalars().all()


@router.post(
    "/requests/{referral_id}/outcome",
    response_model=ReferralOutcomeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def record_referral_outcome(
    referral_id: int,
    data: ReferralOutcomeCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("referral:record_outcome")),
):
    """Records what actually happened at the consult — symptoms, diagnosis,
    prescription, follow-up notes. Recorded by care coordination staff, not
    "the specialist": the AI-recommended specialist's doctor_id comes from
    the mock provider directory's synthetic ID space, not a real platform
    user (see Phase 6/8 notes), so a coordinator relaying the consult report
    is the realistic actor. Moves the referral to `completed` and kicks off
    the whole-care-journey summary in the background."""
    referral = (
        await db.execute(
            select(ReferralRequest).where(ReferralRequest.id == referral_id, ReferralRequest.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if not referral:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Referral not found")

    existing = (
        await db.execute(select(ReferralOutcome).where(ReferralOutcome.referral_request_id == referral_id))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "An outcome has already been recorded for this referral")

    outcome = ReferralOutcome(
        referral_request_id=referral.id, recorded_by_user_id=current_user.id, **data.model_dump()
    )
    db.add(outcome)
    referral.status = ReferralWorkflowStatus.COMPLETED.value

    await log_action(
        db, actor_id=current_user.id, action="referral.outcome.record",
        resource_type="referral_request", resource_id=referral.id,
    )
    await write_outbox_event(
        db, "referral.completed", {"referral_id": referral.id}, referral_id=referral.id,
    )
    await db.commit()
    await db.refresh(outcome)

    background_tasks.add_task(generate_completion_summary, referral.id)
    return outcome


@router.get("/requests/{referral_id}/outcome", response_model=ReferralOutcomeResponse)
async def get_referral_outcome(
    referral_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    await _get_staff_scoped_referral(db, current_user, referral_id)
    outcome = (
        await db.execute(select(ReferralOutcome).where(ReferralOutcome.referral_request_id == referral_id))
    ).scalar_one_or_none()
    if not outcome:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No outcome recorded for this referral")
    return outcome


@router.get("/requests/{referral_id}/events")
async def stream_referral_events(
    referral_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Server-Sent Events stream of this referral's status changes — the
    "real-time referral status updates" hint from the problem statement.
    Scoped by the same visibility rule as everything else: you can't open a
    stream for a referral you couldn't otherwise see."""
    await _get_scoped_referral(db, current_user, referral_id)

    async def event_source():
        async for message in broadcaster.stream(referral_id):
            yield f"data: {message}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
