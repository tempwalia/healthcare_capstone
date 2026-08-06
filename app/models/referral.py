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
    """The consult outcome recorded once a referral's appointment has
    actually happened — symptoms/diagnosis/prescription/follow-up notes.

    Recorded by care coordination staff (see app/core/seed.py), not the
    specialist directly: the AI-recommended specialist's doctor_id comes from
    the mock provider directory's synthetic ID space, not a real platform
    user, so a coordinator relaying the consult report is the realistic
    actor. `interaction_summary` starts null and is filled in asynchronously
    by app/services/referral_outcome.py::generate_completion_summary once
    recorded — a whole-care-journey summary for the next follow-up."""

    __tablename__ = "referral_outcomes"

    id = Column(Integer, primary_key=True, index=True)
    referral_request_id = Column(Integer, ForeignKey("referral_requests.id"), nullable=False, unique=True)
    recorded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    symptoms = Column(Text, nullable=True)
    diagnosis = Column(Text, nullable=True)
    prescription = Column(Text, nullable=True)
    follow_up_notes = Column(Text, nullable=True)
    interaction_summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
