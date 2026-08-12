import enum

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.database.base import Base
from app.models.mixins import SoftDeleteMixin


class ReferralWorkflowStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    INTAKE_PROCESSING = "intake_processing"
    AWAITING_DOCUMENTS = "awaiting_documents"
    ELIGIBILITY_CHECKING = "eligibility_checking"
    ELIGIBILITY_DENIED = "eligibility_denied"
    AWAITING_SPECIALIST_APPROVAL = "awaiting_specialist_approval"
    SCHEDULING = "scheduling"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReferralRequest(Base, SoftDeleteMixin):
    __tablename__ = "referral_requests"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    referring_doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    specialist_id = Column(Integer, ForeignKey("doctors.id"), nullable=True)
    request_date = Column(Date, nullable=False)
    reason = Column(Text, nullable=True)

    status = Column(String(40), default=ReferralWorkflowStatus.SUBMITTED.value, nullable=False)
    target_wait_days = Column(Integer, default=14)
    preferred_location = Column(String(200), nullable=True)
    workflow_thread_id = Column(String(64), nullable=True)
    # The existing medical record (if any) the requester attached at
    # creation time via the unified "New Request" flow.
    medical_record_id = Column(Integer, ForeignKey("medical_records.id"), nullable=True)
    # The specific slot the requester picked for the pre-selected specialist
    # (if any) — see book_real_appointment_node, which prefers this over
    # just grabbing the soonest open slot. Only meaningful alongside
    # specialist_id; a referral with no pre-chosen doctor has no slot to pick.
    # use_alter=True: this closes a 3-table FK cycle (referral_requests ->
    # schedule_slots -> appointments -> referral_requests via referral_id) —
    # without it, neither SQLAlchemy's drop_all nor a plain CREATE TABLE
    # ordering can resolve which table to emit first.
    preferred_slot_id = Column(
        Integer, ForeignKey("schedule_slots.id", use_alter=True, name="fk_referral_requests_preferred_slot_id_schedule_slots"),
        nullable=True,
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ReferralDocument(Base):
    __tablename__ = "referral_documents"

    id = Column(Integer, primary_key=True, index=True)
    referral_request_id = Column(Integer, ForeignKey("referral_requests.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    storage_path = Column(String(500), nullable=False)
    extraction_status = Column(String(30), default="queued")
    extracted_diagnosis_codes = Column(Text, nullable=True)  # JSON-encoded list[str]
    extracted_procedure_codes = Column(Text, nullable=True)  # JSON-encoded list[str]
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SpecialistNote(Base):
    """AI-generated (or manually written) referral-history summary for the
    specialist to read before the consult — see Phase 7 summarizer node."""

    __tablename__ = "specialist_notes"

    id = Column(Integer, primary_key=True, index=True)
    referral_request_id = Column(Integer, ForeignKey("referral_requests.id"), nullable=False)
    note = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ReferralOutcome(Base):
    """The consult outcome recorded once a referral's appointment — or, as
    of the "doctor can complete any booked appointment" pass, ANY
    appointment, referral-linked or not — has actually happened:
    symptoms/diagnosis/prescription/follow-up notes.

    Exactly one of `referral_request_id`/`appointment_id` is set (enforced
    in the route layer, not a DB constraint — SQLite's test backend doesn't
    reliably enforce CHECK constraints the same way Postgres does, so this
    follows the same app-level-validation convention as the rest of this
    codebase's cross-field invariants). The referral path predates the
    appointment path and is unchanged; the appointment path exists because a
    direct-booked appointment (no referral involved at all) previously had
    *no* mechanism for a doctor to record what happened and close it out —
    see app/api/routes/appointments.py's outcome routes.

    Permission to record one is still staff-only — specialist/pcp (for
    appointments)/care_coordinator/doctor hold `referral:record_outcome`
    (see app/core/seed.py) — same rationale as before: whoever is actually
    seeing the patient should be the one recording it, and view_own-scoped
    roles are further restricted to appointments/referrals they're actually
    party to (see _get_scoped_referral / _get_scoped_appointment).
    `interaction_summary` starts null and is filled in asynchronously by
    app/services/referral_outcome.py::generate_completion_summary once
    recorded — a whole-care-journey summary for the next follow-up."""

    __tablename__ = "referral_outcomes"

    id = Column(Integer, primary_key=True, index=True)
    referral_request_id = Column(Integer, ForeignKey("referral_requests.id"), nullable=True, unique=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=True, unique=True)
    recorded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    symptoms = Column(Text, nullable=True)
    diagnosis = Column(Text, nullable=True)
    prescription = Column(Text, nullable=True)
    follow_up_notes = Column(Text, nullable=True)
    interaction_summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
