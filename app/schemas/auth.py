from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, model_validator

from app.models.patient import GenderEnum


class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str

    # Self-service patient onboarding: when set, registration also creates
    # and links a Patient record + grants the `patient` role in the same
    # request, instead of leaving the account inert until an admin acts.
    # Staff roles (pcp/specialist/care_coordinator/payer_admin/admin) are
    # deliberately NOT selectable here — those stay admin-provisioned.
    register_as_patient: bool = False
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[GenderEnum] = None
    # All optional, all patient-onboarding fields a fresh account otherwise
    # starts blank on (see app/core/seed_data.py). insurance_provider/
    # insurance_policy_number get a random demo policy if left blank (same
    # convention as POST /patients/); phone/allergies stay genuinely blank
    # if omitted — there's no eligibility-check dependency forcing a default
    # on those two.
    phone: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_policy_number: Optional[str] = None
    allergies: Optional[str] = None

    @model_validator(mode="after")
    def _patient_fields_required_when_self_registering(self):
        if self.register_as_patient and not all(
            [self.first_name, self.last_name, self.date_of_birth, self.gender]
        ):
            raise ValueError(
                "first_name, last_name, date_of_birth, and gender are required when register_as_patient is true"
            )
        return self


class UserResponse(BaseModel):
    id: int
    email: str
    username: str
    is_active: bool
    is_superuser: bool

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: str


class MeResponse(BaseModel):
    id: int
    username: str
    email: str
    roles: list[str]
    permissions: list[str]
