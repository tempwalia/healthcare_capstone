from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.appointment import AppointmentStatusEnum
from app.models.patient import GenderEnum


class PatientBase(BaseModel):
    first_name: str
    last_name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    date_of_birth: date
    gender: GenderEnum
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_policy_number: Optional[str] = None
    allergies: Optional[str] = None
    blood_type: Optional[str] = None
    preferred_language: Optional[str] = None
    lifestyle: Optional[str] = None
    family_history: Optional[str] = None


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[GenderEnum] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_policy_number: Optional[str] = None
    allergies: Optional[str] = None
    blood_type: Optional[str] = None
    preferred_language: Optional[str] = None
    lifestyle: Optional[str] = None
    family_history: Optional[str] = None


class PatientResponse(PatientBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PatientContextAppointment(BaseModel):
    id: int
    doctor_id: int
    appointment_datetime: datetime
    status: AppointmentStatusEnum
    appointment_type: Optional[str] = None
    reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PatientContextMedicalRecord(BaseModel):
    id: int
    doctor_id: int
    visit_date: datetime
    diagnosis: Optional[str] = None
    treatment: Optional[str] = None
    prescription: Optional[str] = None
    # Doubles as the referral-completion follow-up summary for a
    # `record_type="referral_consult"` row (see
    # app/services/referral_outcome.py::generate_completion_summary) — the
    # whole point of writing that summary into a real medical record instead
    # of only the referral's own outcome tab is for it to actually surface
    # here, on the patient's own chart.
    notes: Optional[str] = None
    record_type: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PatientContextReferral(BaseModel):
    id: int
    status: str
    specialist_id: Optional[int] = None
    request_date: date
    reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PatientContextCareTeamEntry(BaseModel):
    doctor_id: int
    role: str


class PatientContextInsurance(BaseModel):
    provider: Optional[str] = None
    policy_number: Optional[str] = None


class PatientContextDocument(BaseModel):
    id: int
    referral_request_id: int
    filename: str
    extraction_status: str
    extracted_diagnosis_codes: Optional[str] = None
    extracted_procedure_codes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PatientContextResponse(BaseModel):
    patient_id: int
    insurance: PatientContextInsurance
    care_team: List[PatientContextCareTeamEntry]
    appointments: List[PatientContextAppointment]
    medical_records: List[PatientContextMedicalRecord]
    referrals: List[PatientContextReferral]
    documents: List[PatientContextDocument]
