"""DESTRUCTIVE: wipes every data row in the database (users, patients,
doctors, referrals, appointments, medical records, notifications, audit
logs, LangGraph checkpoints — everything except the schema itself and the
roles/permissions reference tables) and replaces it with exactly:

    - 1 admin account          (needed to administer the platform at all —
                                 not part of the user's requested count, kept
                                 minimal and called out explicitly)
    - 2 care_coordinator accounts
    - 10 doctor accounts (specialist role, full profile, no availability/
      slots — deliberately no scheduling data, per "don't create any other
      records for now")
    - 10 patient accounts (full profile, no referrals/appointments/records)

Every account shares one password. Writes LOGIN_CREDENTIALS.md (repo root)
listing username/password/role for all of them.

Run once, deliberately, from an interactive terminal — this is not meant to
be part of any automated startup/seed path:

    uv run python scripts/reset_demo_data.py
"""
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from app.core.security import get_password_hash  # noqa: E402
from app.core.seed import seed_roles_and_permissions  # noqa: E402
from app.database.session import async_session  # noqa: E402
from app.models.doctor import Doctor  # noqa: E402
from app.models.patient import GenderEnum, Patient  # noqa: E402
from app.models.role import Role, UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.insurance import random_policy  # noqa: E402

PASSWORD = "teste@123"
REPO_ROOT = Path(__file__).resolve().parent.parent

# Tables holding real data — everything except alembic_version (migration
# tracking), roles/permissions/role_permissions (reference data, reseeded
# below anyway), and checkpoint_migrations (the checkpointer's own schema
# version table, not data).
DATA_TABLES = [
    "referral_documents", "referral_outcomes", "specialist_notes", "referral_requests",
    "appointments", "medical_records", "schedule_slots", "doctor_availability",
    "doctor_insurance_networks", "insurance_plans", "provider_directory_links",
    "notifications", "outbox_events", "audit_logs", "refresh_tokens",
    "checkpoints", "checkpoint_writes", "checkpoint_blobs",
    "user_roles", "doctors", "patients", "users",
]

DOCTORS = [
    {"first_name": "Priya", "last_name": "Rao", "specialization": "Orthopedics", "external_doctor_id": 88,
     "license_number": "MD-DUMMY-088", "years_of_experience": 14, "department": "Orthopedics",
     "bio": "Orthopedic surgeon focused on sports injuries and joint reconstruction."},
    {"first_name": "Daniel", "last_name": "Kim", "specialization": "Orthopedics", "external_doctor_id": 91,
     "license_number": "MD-DUMMY-091", "years_of_experience": 9, "department": "Orthopedics",
     "bio": "Orthopedic specialist with a focus on spine and back pain."},
    {"first_name": "Maria", "last_name": "Chen", "specialization": "Cardiology", "external_doctor_id": 12,
     "license_number": "MD-DUMMY-012", "years_of_experience": 18, "department": "Cardiology",
     "bio": "Cardiologist specializing in preventive care and arrhythmia management."},
    {"first_name": "James", "last_name": "Okoye", "specialization": "Cardiology", "external_doctor_id": 45,
     "license_number": "MD-DUMMY-045", "years_of_experience": 11, "department": "Cardiology",
     "bio": "Cardiologist with a focus on hypertension and heart failure management."},
    {"first_name": "Lena", "last_name": "Novak", "specialization": "Dermatology", "external_doctor_id": 67,
     "license_number": "MD-DUMMY-067", "years_of_experience": 7, "department": "Dermatology",
     "bio": "Dermatologist specializing in skin conditions and minor procedures."},
    {"first_name": "Omar", "last_name": "Farouk", "specialization": "Orthopedics", "external_doctor_id": 73,
     "license_number": "MD-DUMMY-073", "years_of_experience": 20, "department": "Orthopedics",
     "bio": "Senior orthopedic consultant, joint replacement and trauma."},
    {"first_name": "Sarah", "last_name": "Whitfield", "specialization": "Neurology", "external_doctor_id": None,
     "license_number": "MD-DUMMY-101", "years_of_experience": 13, "department": "Neurology",
     "bio": "Neurologist specializing in headache disorders and nerve conditions."},
    {"first_name": "Ahmed", "last_name": "Hassan", "specialization": "Family Medicine", "external_doctor_id": None,
     "license_number": "MD-DUMMY-102", "years_of_experience": 16, "department": "Family Medicine",
     "bio": "Family medicine physician providing primary and preventive care."},
    {"first_name": "Grace", "last_name": "Park", "specialization": "Family Medicine", "external_doctor_id": None,
     "license_number": "MD-DUMMY-103", "years_of_experience": 6, "department": "Family Medicine",
     "bio": "Family medicine physician focused on chronic disease management."},
    {"first_name": "Marcus", "last_name": "Bell", "specialization": "General Practice", "external_doctor_id": None,
     "license_number": "MD-DUMMY-104", "years_of_experience": 10, "department": "General Practice",
     "bio": "General practitioner covering routine checkups and referrals."},
]

PATIENTS = [
    {"first_name": "Alex", "last_name": "Morgan", "gender": GenderEnum.MALE, "dob": date(1985, 3, 14)},
    {"first_name": "Priya", "last_name": "Sharma", "gender": GenderEnum.FEMALE, "dob": date(1990, 7, 22)},
    {"first_name": "Wei", "last_name": "Chen", "gender": GenderEnum.MALE, "dob": date(1978, 11, 5)},
    {"first_name": "Fatima", "last_name": "Ahmed", "gender": GenderEnum.FEMALE, "dob": date(1995, 1, 30)},
    {"first_name": "Diego", "last_name": "Martinez", "gender": GenderEnum.MALE, "dob": date(1988, 9, 12)},
    {"first_name": "Sarah", "last_name": "Johnson", "gender": GenderEnum.FEMALE, "dob": date(1972, 5, 18)},
    {"first_name": "Kwame", "last_name": "Mensah", "gender": GenderEnum.MALE, "dob": date(1993, 12, 2)},
    {"first_name": "Elena", "last_name": "Rossi", "gender": GenderEnum.FEMALE, "dob": date(1982, 6, 25)},
    {"first_name": "Ravi", "last_name": "Patel", "gender": GenderEnum.MALE, "dob": date(1999, 4, 8)},
    {"first_name": "Nina", "last_name": "Kowalski", "gender": GenderEnum.FEMALE, "dob": date(1965, 10, 19)},
]

COORDINATORS = [
    {"first_name": "Beth", "last_name": "Coleman"},
    {"first_name": "Marcus", "last_name": "Diaz"},
]


def _username(first: str, last: str, prefix: str = "") -> str:
    return f"{prefix}{first.lower()}.{last.lower()}"


async def main() -> None:
    confirm = input(
        "This PERMANENTLY deletes every user/patient/doctor/referral/appointment/etc. row "
        "in the connected database and replaces them with a fresh 1 admin + 2 coordinator + "
        "10 doctor + 10 patient demo set. Type 'yes' to continue: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted — no changes made.")
        return

    async with async_session() as db:
        await db.execute(text(f"TRUNCATE TABLE {', '.join(DATA_TABLES)} RESTART IDENTITY CASCADE"))
        await db.commit()
        print(f"Truncated {len(DATA_TABLES)} tables.\n")

        await seed_roles_and_permissions(db)
        roles = {r.name: r for r in (await db.execute(select(Role))).scalars().all()}
        print("Roles/permissions reseeded.\n")

        credentials = []

        async def make_user(username: str, email: str, role_name: str) -> User:
            user = User(email=email, username=username, hashed_password=get_password_hash(PASSWORD))
            db.add(user)
            await db.flush()
            db.add(UserRole(user_id=user.id, role_id=roles[role_name].id))
            return user

        # --- Admin ---
        await make_user("admin", "admin@demo.example.com", "admin")
        credentials.append(("admin", "admin", PASSWORD))

        # --- Care coordinators ---
        for c in COORDINATORS:
            username = _username(c["first_name"], c["last_name"])
            await make_user(username, f"{username}@demo.example.com", "care_coordinator")
            credentials.append((username, "care_coordinator", PASSWORD))

        # --- Doctors (specialist role) ---
        for spec in DOCTORS:
            # Just the first name, lowercase, no dots/prefix — the
            # dr.firstname.lastname format was reported as hard to log in
            # with (easy to mistype/misremember). No collision risk: all 10
            # doctor first names are distinct from each other, and patients/
            # coordinators keep their firstname.lastname usernames, which
            # never collide with a bare first name.
            username = spec["first_name"].lower()
            email = f"{username}@demo.example.com"
            user = await make_user(username, email, "specialist")
            doctor = Doctor(
                user_id=user.id, first_name=spec["first_name"], last_name=spec["last_name"], email=email,
                phone="555-100-2000", specialization=spec["specialization"], license_number=spec["license_number"],
                years_of_experience=spec["years_of_experience"], bio=spec["bio"],
                certifications="Board Certified", languages_spoken="English", ratings=5,
                department=spec["department"],
            )
            db.add(doctor)
            credentials.append((username, "specialist", PASSWORD))

        # --- Patients ---
        for p in PATIENTS:
            username = _username(p["first_name"], p["last_name"])
            email = f"{username}@demo.example.com"
            user = await make_user(username, email, "patient")
            policy_number, provider = random_policy()
            patient = Patient(
                user_id=user.id, first_name=p["first_name"], last_name=p["last_name"], email=email,
                phone="555-200-3000", date_of_birth=p["dob"], gender=p["gender"],
                insurance_provider=provider, insurance_policy_number=policy_number,
            )
            db.add(patient)
            credentials.append((username, "patient", PASSWORD))

        await db.commit()

    lines = [
        "# Demo Login Credentials",
        "",
        f"Generated by `scripts/reset_demo_data.py`. Every account uses the password `{PASSWORD}`.",
        "",
        "| Username | Role | Password |",
        "|---|---|---|",
    ]
    for username, role, password in credentials:
        lines.append(f"| {username} | {role} | {password} |")
    lines.append("")
    lines.append(
        "No referrals, appointments, medical records, or doctor availability/slots were created — "
        "just these accounts, per request."
    )
    lines.append("")
    lines.append(
        "Note: doctors priya, daniel, maria, james, lena, and omar share their name/specialty with the "
        "AI recommendation engine's mock candidates, "
        "but the actual ProviderDirectoryLink mapping (needed for an AI-recommended specialist to "
        "resolve to one of these real logins) was wiped along with everything else and was not "
        "recreated. Run `uv run python scripts/seed_dummy_doctors.py` if you want that mapping and "
        "default Mon-Fri availability/slots back for these doctors."
    )
    (REPO_ROOT / "LOGIN_CREDENTIALS.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"Created {len(credentials)} accounts: 1 admin, {len(COORDINATORS)} care_coordinator, "
          f"{len(DOCTORS)} doctor (specialist role), {len(PATIENTS)} patient.")
    print(f"Credentials written to {REPO_ROOT / 'LOGIN_CREDENTIALS.md'}")


if __name__ == "__main__":
    asyncio.run(main())
