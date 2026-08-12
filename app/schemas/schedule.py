from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DoctorAvailabilityBase(BaseModel):
    doctor_id: int
    weekday: int = Field(ge=0, le=6, description="0=Monday .. 6=Sunday")
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$", description="HH:MM, 24h")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$", description="HH:MM, 24h")
    slot_minutes: Optional[int] = 30


class DoctorAvailabilityCreate(DoctorAvailabilityBase):
    pass


class DoctorAvailabilityResponse(DoctorAvailabilityBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScheduleSlotResponse(BaseModel):
    id: int
    doctor_id: int
    starts_at: datetime
    ends_at: datetime
    is_booked: bool
    appointment_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class GenerateSlotsRequest(BaseModel):
    doctor_id: int
    days_ahead: int = Field(default=14, ge=1, le=60)


class BookSlotRequest(BaseModel):
    patient_id: int
    reason: Optional[str] = None
    # An existing medical record the requester attached at booking time —
    # see app.services.record_scope.validate_medical_record_for_patient.
    medical_record_id: Optional[int] = None
