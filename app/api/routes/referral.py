import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_active_user, require_permission
from app.api.dependencies.database import get_async_session
from app.api.dependencies.pagination import build_page
from app.core.time_utils import ensure_aware
from app.events import broadcaster
from app.events.outbox import write_outbox_event
from app.models.doctor import Doctor
from app.models.medical_record import MedicalRecord, MedicalRecordDocument
from app.models.outbox import OutboxEvent
from app.models.patient import Patient
from app.models.referral import (
    ReferralDocument,
    ReferralOutcome,
    ReferralRequest,
    ReferralWorkflowStatus,
    SpecialistNote,
)
from app.models.schedule import ScheduleSlot
from app.models.user import User
from app.schemas.common import Page
from app.schemas.medical_record import AttachedMedicalRecordResponse
from app.schemas.referral import (
    ReferralDocumentResponse,
    ReferralOutcomeCreate,
    ReferralOutcomeResponse,
    ReferralRequestCreate,
    ReferralRequestResponse,
    ReferralRequestUpdate,
    SpecialistNoteCreate,
    SpecialistNoteResponse,
    TimelineEventResponse,
)
from app.services.audit import log_action
from app.services.notifications import create_notification
from app.services.record_scope import _granted_permissions, validate_medical_record_for_patient
from app.services.referral_outcome import generate_completion_summary
from app.services.referral_scope import referral_visibility_filter
from app.services.referral_workflow import run_referral_workflow
from app.services.storage import save_referral_document

# Referral edits/deletes are a coordinator/specialist/admin action, not a
# self-service one — matches the frontend's own `canEdit` gate
# (static/js/modules/referrals.js), which already hides the Edit/Delete
# controls from anyone without one of these. Without this check on the API
# side, `get_current_active_user` plus ownership scoping alone would let a
# referral's own patient/pcp/specialist PATCH its `status`/`specialist_id`
# directly — a side channel around the `referral:approve`-gated
# `/referral-workflow/{id}/resume` human-in-the-loop approval step.
_EDIT_PERMISSIONS = {"referral:approve", "referral:override", "admin:*"}

router = APIRouter(prefix="/referral", tags=["referral"])

# outbox_events (app/models/outbox.py) is a permanent, append-only table —
# rows are marked published_at, never deleted — so every milestone written
# alongside a referral's status changes doubles as a durable timeline. Human
# labels for the timeline endpoint below; anything not in this map falls
# back to its raw event_type.
_TIMELINE_EVENT_LABELS = {
    "referral.submitted": "Referral Submitted",
    "referral.status.changed": "Status Changed",
    "referral.eligibility.verified": "Insurance Verified",
    "referral.eligibility.denied": "Insurance Denied",
    "referral.eligibility.escalated": "Eligibility Escalated for Review",
    "referral.specialist.recommended": "Specialist Candidates Recommended",
    "referral.appointment.scheduled": "Appointment Scheduled",
    "referral.delay.predicted": "Scheduling Delay Predicted",
    "referral.completed": "Referral Completed",
}


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
    if data.medical_record_id is not None:
        await validate_medical_record_for_patient(db, data.medical_record_id, data.patient_id)
    if data.preferred_slot_id is not None:
        if data.specialist_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "preferred_slot_id requires a specialist_id")
        slot = await db.get(ScheduleSlot, data.preferred_slot_id)
        if slot is None or slot.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Preferred slot not found")
        if slot.doctor_id != data.specialist_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Preferred slot does not belong to the selected specialist")
        if slot.is_booked:
            raise HTTPException(status.HTTP_409_CONFLICT, "Preferred slot is already booked")
        if ensure_aware(slot.starts_at) < datetime.now(timezone.utc):
            raise HTTPException(status.HTTP_409_CONFLICT, "Preferred slot is in the past")

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
    # Deliberately `List[str] = Query([])`, not `Optional[List[str]] = Query(None)`:
    # the installed fastapi_mcp (app/../.venv/Lib/site-packages/fastapi_mcp/openapi/convert.py
    # ~L227) injects a spurious top-level `"type": "array"` onto any query-param
    # schema that lacks a top-level `type` key — which an `anyOf: [array, null]`
    # schema (what Optional[List[str]] produces) always does. The result is a
    # tool schema that requires the value be BOTH `anyOf [array, null]` AND
    # `type: array`, so a valid `null` (an LLM's normal way of saying "omit this
    # optional filter") gets rejected as "expected array, but got null" — this
    # is exactly the assistant 500 a patient hit asking about referral status.
    # A concrete `[]` default keeps the schema a plain `{"type": "array"}` with
    # no anyOf, so that injection is a no-op and the field stays legitimately
    # optional (not required, empty list treated as "no filter" below).
    status_filter: List[str] = Query([]),
    # Same fastapi_mcp anyOf/null-injection bug as status_filter above, just
    # for a plain Optional[str] this time — get_single_param_type_from_schema
    # (app/../.venv/Lib/site-packages/fastapi_mcp/openapi/utils.py) strips
    # "null" out of `anyOf: [string, null]` and injects the remaining
    # "string" as a contradictory top-level `type`, so an LLM's explicit
    # `q: null` (its normal way of saying "no search term") gets rejected as
    # "expected string, but got null". This is the exact bug a patient hit
    # asking the assistant "any referral pending" / a bare status question —
    # `q: str = Query("")` keeps the schema a plain `{"type": "string"}` with
    # no anyOf, so the injection is a no-op.
    q: str = Query(""),
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """`q` free-text searches within whatever this caller's visibility scope
    already allows — never a broader search than what list_referrals would
    return unfiltered, just a narrower one. Matches the reason/preferred_location
    text or an exact numeric id, so "knee" or "referral #42" both work."""
    scope = await referral_visibility_filter(db, current_user)
    base_query = select(ReferralRequest).where(ReferralRequest.deleted_at.is_(None))
    if scope is not None:
        base_query = base_query.where(scope)
    if status_filter:
        base_query = base_query.where(ReferralRequest.status.in_(status_filter))
    if q and q.strip():
        term = q.strip()
        conditions = [
            ReferralRequest.reason.ilike(f"%{term}%"),
            ReferralRequest.preferred_location.ilike(f"%{term}%"),
        ]
        if term.lstrip("#").isdigit():
            conditions.append(ReferralRequest.id == int(term.lstrip("#")))
        base_query = base_query.where(or_(*conditions))

    total = (await db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
    result = await db.execute(base_query.order_by(ReferralRequest.id.desc()).offset(skip).limit(limit))
    return build_page(request, result.scalars().all(), total, skip, limit)


# Registered before `/requests/{referral_id}` deliberately: that route's
# `referral_id: int` conversion happens *after* Starlette's path match, not
# during it, so if `{referral_id}` were registered first, a request for this
# literal "ops-queue" segment would match it first and 422 on int conversion
# rather than ever reaching this route.
@router.get("/requests/ops-queue", response_model=Page[ReferralRequestResponse], operation_id="list_ops_queue_referrals")
async def list_ops_queue_referrals(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("referral:approve")),
):
    """The care coordinator worklist: referrals that need a human decision
    right now, bucketed by what's blocking them — awaiting a specialist
    pick, denied eligibility needing escalation, or scheduled with no
    consult outcome recorded yet. Same visibility filter as list_referrals
    for defense-in-depth, even though referral:approve holders (coordinator/
    admin) already resolve it to "no restriction" today."""
    outcome_exists = (
        select(ReferralOutcome.id)
        .where(ReferralOutcome.referral_request_id == ReferralRequest.id)
        .exists()
    )
    base_query = select(ReferralRequest).where(
        ReferralRequest.deleted_at.is_(None),
        or_(
            ReferralRequest.status == ReferralWorkflowStatus.AWAITING_SPECIALIST_APPROVAL.value,
            ReferralRequest.status == ReferralWorkflowStatus.ELIGIBILITY_DENIED.value,
            and_(ReferralRequest.status == ReferralWorkflowStatus.SCHEDULED.value, ~outcome_exists),
        ),
    )
    scope = await referral_visibility_filter(db, current_user)
    if scope is not None:
        base_query = base_query.where(scope)

    total = (await db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
    result = await db.execute(base_query.order_by(ReferralRequest.updated_at.asc()).offset(skip).limit(limit))
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
    granted = await _granted_permissions(db, current_user)
    if not granted & _EDIT_PERMISSIONS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Editing a referral requires referral:approve, referral:override, or admin privileges",
        )

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
    granted = await _granted_permissions(db, current_user)
    if not granted & _EDIT_PERMISSIONS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Deleting a referral requires referral:approve, referral:override, or admin privileges",
        )
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
    # Every referral document also lands on the patient's own medical
    # record — "attached to the specific referral AND the patient record",
    # not just filed under the referral where only referral-scoped staff
    # would ever see it again. doctor_id is the referring doctor (always a
    # real FK on the referral itself); a patient/coordinator uploading
    # during eligibility-denial review goes through this exact same path.
    db.add(MedicalRecord(
        patient_id=referral.patient_id,
        doctor_id=referral.referring_doctor_id,
        visit_date=datetime.now(timezone.utc),
        record_type="referral_document",
        notes=f"Document \"{file.filename or 'upload'}\" uploaded for referral #{referral.id}.",
    ))
    await db.flush()
    await log_action(
        db, actor_id=current_user.id, action="referral.document.upload",
        resource_type="referral_document", resource_id=document.id,
        details={"referral_id": referral.id, "filename": document.filename},
    )
    await db.commit()
    await db.refresh(document)

    # Documents are optional (a filled-in reason is sufficient on its own —
    # see intake_node), so the workflow graph typically reaches intake and
    # moves on before a document upload could land at all. If a document
    # does arrive while the referral is still in one of these early states —
    # not yet past specialist recommendation, so nothing's paused on a
    # pending human-in-the-loop decision yet — re-running picks it up for
    # real extraction instead of leaving it un-processed. Deliberately
    # excludes ELIGIBILITY_DENIED: that status now means the workflow is
    # genuinely paused (interrupt()) waiting on a coordinator's explicit
    # POST /referral-workflow/{id}/override-eligibility decision, not a
    # silent auto-retry — a document uploaded during review attaches here
    # (and to the patient record above) without side-stepping that review.
    _REPROCESSABLE_ON_UPLOAD = {
        ReferralWorkflowStatus.SUBMITTED.value,
        ReferralWorkflowStatus.INTAKE_PROCESSING.value,
        ReferralWorkflowStatus.AWAITING_DOCUMENTS.value,
        ReferralWorkflowStatus.ELIGIBILITY_CHECKING.value,
    }
    if referral.status in _REPROCESSABLE_ON_UPLOAD:
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


@router.get("/requests/{referral_id}/documents/{document_id}/download")
async def download_referral_document(
    referral_id: int,
    document_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Lets anyone who can already see this referral (referring doctor,
    assigned specialist, the patient, or staff) actually open an uploaded
    document, not just see its filename in the list — a doctor inspecting a
    referral needs to read the referral letter/imaging report, not just
    know one exists."""
    referral = await _get_scoped_referral(db, current_user, referral_id)
    document = await db.get(ReferralDocument, document_id)
    if document is None or document.referral_request_id != referral.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    path = Path(document.storage_path)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found on disk")
    return FileResponse(path, filename=document.filename)


@router.get("/requests/{referral_id}/attached-record", response_model=AttachedMedicalRecordResponse)
async def get_referral_attached_record(
    referral_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """The medical record (if any) the requester picked or uploaded when
    creating this referral — separate from the referral's own
    ReferralDocuments (uploaded via /documents above). Visibility is
    entirely derived from being able to see the referral itself, not the
    record's own doctor_id — see app.services.document_access."""
    referral = await _get_scoped_referral(db, current_user, referral_id)
    if referral.medical_record_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No medical record attached to this referral")

    record = await db.get(MedicalRecord, referral.medical_record_id)
    if record is None or record.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attached medical record not found")

    documents = (
        await db.execute(
            select(MedicalRecordDocument)
            .where(MedicalRecordDocument.medical_record_id == record.id)
            .order_by(MedicalRecordDocument.created_at)
        )
    ).scalars().all()
    return {"record": record, "documents": documents}


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
    "/requests/{referral_id}/notes",
    response_model=SpecialistNoteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_specialist_note(
    referral_id: int,
    data: SpecialistNoteCreate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Manual comment on a referral — same gate as editing the referral
    itself (_EDIT_PERMISSIONS): coordinator/specialist/admin, not the
    patient or referring PCP self-service side. Needed for the eligibility-
    denial review flow (a coordinator explains why they're overriding a
    denial), but not restricted to that status — any referral can carry
    notes, same as the pre-existing workflow-generated ones."""
    referral = await _get_scoped_referral(db, current_user, referral_id)
    granted = await _granted_permissions(db, current_user)
    if not granted & _EDIT_PERMISSIONS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Adding a note requires referral:approve, referral:override, or admin privileges",
        )

    note = SpecialistNote(referral_request_id=referral.id, note=data.note)
    db.add(note)
    await log_action(
        db, actor_id=current_user.id, action="referral.note.create",
        resource_type="referral_request", resource_id=referral.id,
    )
    await db.commit()
    await db.refresh(note)
    return note


@router.get(
    "/requests/{referral_id}/timeline",
    response_model=List[TimelineEventResponse],
    operation_id="get_referral_timeline",
)
async def get_referral_timeline(
    referral_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Reads back the durable outbox_events trail for this referral — the
    milestone-by-milestone history (submitted, eligibility, specialist
    recommendation, scheduling, completion) that's been written all along but
    had no route surfacing it until now. Scoped identically to the referral
    itself, same as the documents/notes sub-routes above."""
    referral = await _get_scoped_referral(db, current_user, referral_id)
    result = await db.execute(
        select(OutboxEvent).where(OutboxEvent.referral_id == referral.id).order_by(OutboxEvent.created_at)
    )
    return [
        TimelineEventResponse(
            event_type=event.event_type,
            label=_TIMELINE_EVENT_LABELS.get(event.event_type, event.event_type),
            payload=json.loads(event.payload),
            created_at=event.created_at,
        )
        for event in result.scalars().all()
    ]


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
    prescription, follow-up notes. `specialist` now also holds
    referral:record_outcome (a real platform doctor can be mapped onto a
    referral's specialist_id via ProviderDirectoryLink at approval time — see
    resume_workflow — making "the specialist completes their own referral" a
    real, working flow, not just care-coordinator relay). Uses
    _get_scoped_referral, not a raw unscoped lookup: `referral:record_outcome`
    alone would otherwise let a `view_own`-scoped specialist record an
    outcome for a referral they were never assigned to (view_all-scoped
    roles — care_coordinator/doctor/admin — are unaffected, same as every
    other _get_scoped_referral caller). Moves the referral to `completed`
    and kicks off the whole-care-journey summary in the background."""
    referral = await _get_scoped_referral(db, current_user, referral_id)

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

    referring_doctor = await db.get(Doctor, referral.referring_doctor_id)
    if referring_doctor is not None and referring_doctor.user_id is not None:
        await create_notification(
            db, user_id=referring_doctor.user_id, title="Outcome recorded for your referral",
            body=f"A consult outcome has been recorded for referral #{referral.id}.",
            referral_id=referral.id,
        )
    else:
        # Same best-effort skip as notify_node's patient-side case — now
        # audit-logged too, matching eligibility_node's equivalent skip.
        await log_action(
            db, actor_id=None, action="referral.notification.skipped",
            resource_type="referral_request", resource_id=referral.id,
            details={"reason": "referring doctor has no linked user account", "event": "outcome_recorded"},
        )

    await db.commit()
    await db.refresh(outcome)

    background_tasks.add_task(generate_completion_summary, outcome.id)
    return outcome


@router.get("/requests/{referral_id}/outcome", response_model=ReferralOutcomeResponse)
async def get_referral_outcome(
    referral_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Same visibility as the referral itself: once a consult outcome is
    recorded, everyone who could already see the referral — the patient it
    belongs to, the referring doctor, the assigned specialist, or staff with
    referral:view_all — can read the resulting summary too. Recording an
    outcome (POST, above) stays staff-only via referral:record_outcome;
    this is read-only."""
    await _get_scoped_referral(db, current_user, referral_id)
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
