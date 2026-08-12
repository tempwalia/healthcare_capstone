from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class MedicalRecordBase(BaseModel):
    patient_id: int
    # Optional: a patient's own direct document upload has no treating
    # doctor to name — see POST /medical-records/quick-upload.
    doctor_id: Optional[int] = None
    visit_date: datetime
    diagnosis: Optional[str] = None
    symptoms: Optional[str] = None
    treatment: Optional[str] = None
    prescription: Optional[str] = None
    notes: Optional[str] = None
    blood_pressure_systolic: Optional[int] = None
    blood_pressure_diastolic: Optional[int] = None
    heart_rate: Optional[int] = None
    temperature: Optional[float] = None
    weight: Optional[float] = None
    height: Optional[float] = None
    record_type: Optional[str] = None
    attachments: Optional[str] = None
    version: Optional[int] = 1
    access_log: Optional[str] = None


class MedicalRecordCreate(MedicalRecordBase):
    pass


class MedicalRecordUpdate(BaseModel):
    diagnosis: Optional[str] = None
    symptoms: Optional[str] = None
    treatment: Optional[str] = None
    prescription: Optional[str] = None
    notes: Optional[str] = None
    blood_pressure_systolic: Optional[int] = None
    blood_pressure_diastolic: Optional[int] = None
    heart_rate: Optional[int] = None
    temperature: Optional[float] = None
    weight: Optional[float] = None
    height: Optional[float] = None
    record_type: Optional[str] = None
    attachments: Optional[str] = None
    version: Optional[int] = None
    access_log: Optional[str] = None


class MedicalRecordResponse(MedicalRecordBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MedicalRecordDocumentResponse(BaseModel):
    id: int
    medical_record_id: int
    filename: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AttachedMedicalRecordResponse(BaseModel):
    """The medical record (and its documents) a referral/appointment has
    attached — returned by GET .../attached-record so a doctor scoped to
    see that referral/appointment can inspect it, even if they aren't the
    record's own `doctor_id`. See app.services.document_access."""

    record: MedicalRecordResponse
    documents: List[MedicalRecordDocumentResponse]
