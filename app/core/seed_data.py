"""Randomized default-data generation for a brand-new self-service patient
registration. A fresh account otherwise starts with none of phone/insurance/
policy number/allergies filled in (care team, referrals, and lab/medical
records are deliberately NOT faked here — those are derived from real usage,
not registration defaults, per app/services/patient_context.py). Used by
both GET /auth/sample-patient-data (the "Fill Sample Data" button's preview,
editable before submit) and POST /auth/register's own insurance fallback
(mirrors app/api/routes/patients.py::create_patient's existing "blank
insurance gets a random demo policy" convention, so a self-registered
patient's referrals can pass eligibility just as reliably as an
admin-created one)."""
import random
from datetime import date, timedelta
from typing import Optional

from app.models.patient import GenderEnum
from app.services.insurance import random_policy

FIRST_NAMES = ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Avery", "Priya", "Wei", "Fatima", "Diego"]
LAST_NAMES = ["Nguyen", "Smith", "Garcia", "Patel", "Kim", "Johnson", "Rossi", "Mueller", "Okafor", "Lopez", "Chen", "Brown"]

SAMPLE_ALLERGIES = [
    "No known allergies", "Penicillin", "Peanuts", "Latex", "Shellfish", "Pollen (seasonal)", "Sulfa drugs",
]
BLOOD_TYPES = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]


def _random_phone() -> str:
    return f"555-{random.randint(200, 999)}-{random.randint(1000, 9999)}"


def _random_date_of_birth() -> date:
    age_days = random.randint(18 * 365, 75 * 365)
    return date.today() - timedelta(days=age_days)


def random_patient_profile(*, email_stamp: Optional[str] = None) -> dict:
    """A full randomized registration payload — safe to send back to an
    unauthenticated client (no PII about any real person, purely synthetic),
    and safe to use server-side as a fallback default. `email_stamp` lets
    the caller keep the email in sync with a username it already generated;
    left None, a fresh one is minted here."""
    stamp = email_stamp or str(random.randint(1000000, 9999999))
    first_name = random.choice(FIRST_NAMES)
    last_name = random.choice(LAST_NAMES)
    policy_number, provider = random_policy()
    return {
        "first_name": first_name,
        "last_name": last_name,
        "email": f"{first_name.lower()}.{last_name.lower()}.{stamp}@example.com",
        "date_of_birth": _random_date_of_birth().isoformat(),
        "gender": random.choice([g.value for g in GenderEnum]),
        "phone": _random_phone(),
        "insurance_provider": provider,
        "insurance_policy_number": policy_number,
        "allergies": random.choice(SAMPLE_ALLERGIES),
        "blood_type": random.choice(BLOOD_TYPES),
    }
