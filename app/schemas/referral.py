from datetime import date, datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class ReferralRequestBase(BaseModel):
    patient_id: int
    referring_doctor_id: int
    specialist_id: Optional[int] = None
    request_date: date
    reason: Optional[str] = None
    preferred_location: Optional[str] = None
    target_wait_days: Optional[int] = 14


class ReferralRequestCreate(ReferralRequestBase):
    pass


class ReferralRequestUpdate(BaseModel):
    specialist_id: Optional[int] = None
    reason: Optional[str] = None
    preferred_location: Optional[str] = None
    target_wait_days: Optional[int] = None
    status: Optional[str] = None


class ReferralRequestResponse(ReferralRequestBase):
    id: int
    status: str
    workflow_thread_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ReferralDocumentResponse(BaseModel):
    id: int
    referral_request_id: int
    filename: str
    extraction_status: str
    extracted_diagnosis_codes: Optional[str] = None
    extracted_procedure_codes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SpecialistNoteCreate(BaseModel):
    note: str


class ResumeDecision(BaseModel):
    """Body for `POST /referral-workflow/{id}/resume` — the human-in-the-loop
    decision that unpauses the graph at `await_specialist_approval`."""

    doctor_id: int
    # Optional: maps the mock directory's synthetic `doctor_id` above onto a
    # real platform Doctor row (creates/overwrites a ProviderDirectoryLink).
    # Omitted entirely, resume behaves exactly as before this existed.
    platform_doctor_id: Optional[int] = None


class ProviderDirectoryLinkResponse(BaseModel):
    id: int
    source_system: str
    external_doctor_id: int
    doctor_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SpecialistNoteResponse(BaseModel):
    id: int
    referral_request_id: int
    note: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReferralOutcomeCreate(BaseModel):
    symptoms: Optional[str] = None
    diagnosis: Optional[str] = None
    prescription: Optional[str] = None
    follow_up_notes: Optional[str] = None


class ReferralOutcomeResponse(ReferralOutcomeCreate):
    id: int
    # Exactly one of these two is set — see app/models/referral.py's
    # ReferralOutcome docstring. Both Optional now that a consult outcome
    # can be recorded against a direct-booked appointment with no referral
    # involved at all (app/api/routes/appointments.py's outcome routes).
    referral_request_id: Optional[int] = None
    appointment_id: Optional[int] = None
    recorded_by_user_id: int
    interaction_summary: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TimelineEventResponse(BaseModel):
    event_type: str
    label: str
    payload: Dict[str, Any]
    created_at: datetime
