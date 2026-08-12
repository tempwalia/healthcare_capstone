"""Dev/manual-testing utility: seeds exactly 10 fully-featured dummy doctor
accounts — real User logins (all sharing one demo password), linked Doctor
directory rows with every field filled in, the `specialist` role (which now
holds referral:record_outcome — see app/core/seed.py), a default Mon-Fri 9-5
availability schedule with slots generated, and, for the 6 whose name/
specialty matches an entry in the mock provider directory
(mock_systems/provider_directory_mock/main.py::PROVIDERS), a
ProviderDirectoryLink pre-mapping it to a real login.

Why this matters end-to-end: without a ProviderDirectoryLink, an AI-recommended
specialist candidate is just a synthetic id with no real account behind it —
"the assigned doctor logs in and completes the referral" has nothing to log
into. Pre-linking these 10 means a referral recommending "Dr. Priya Rao" (mock
candidate 88) resolves, once approved, straight to a real, loggable-in
account that can see the referral and record its outcome.

Idempotent — safe to re-run; matches existing rows by username instead of
creating duplicates. Does NOT touch/remove any other existing doctor rows
(the platform doesn't enforce "exactly 10" as a hard cap — this only
guarantees these 10 exist and are fully usable).

    uv run python scripts/seed_dummy_doctors.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.api.routes.schedule import _generate_slots  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402
from app.database.session import async_session  # noqa: E402
from app.models.doctor import Doctor  # noqa: E402
from app.models.provider_directory_link import ProviderDirectoryLink  # noqa: E402
from app.models.role import Role, UserRole  # noqa: E402
from app.models.schedule import DoctorAvailability  # noqa: E402
from app.models.user import User  # noqa: E402

DUMMY_PASSWORD = "waliat2@"
WEEKDAYS_MON_TO_FRI = range(5)
DAYS_AHEAD = 14

# The first 6 deliberately mirror mock_systems/provider_directory_mock's
# PROVIDERS list (name + specialty + external_doctor_id) so they can be
# pre-linked; the last 4 round out specialties the mock directory doesn't
# cover (useful as referring/PCP-style doctors and for direct booking).
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


async def main() -> None:
    async with async_session() as db:
        role = (await db.execute(select(Role).where(Role.name == "specialist"))).scalar_one_or_none()
        if role is None:
            print("The 'specialist' role isn't seeded yet — run scripts/seed_roles.py first.")
            return

        admin_user = (
            await db.execute(
                select(User).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
                .where(Role.name == "admin")
            )
        ).scalars().first()

        created, updated = 0, 0
        for spec in DOCTORS:
            # Matches scripts/reset_demo_data.py's convention (first name
            # only, lowercase, no dr./dots) — dr.firstname.lastname was
            # reported as hard to log in with. Keep these two scripts' doctor
            # usernames in sync so re-running either one finds the same
            # accounts instead of creating duplicates.
            username = spec["first_name"].lower()
            email = f"{username}@dummy-doctors.example.com"

            user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
            is_new_user = user is None
            if user is None:
                user = User(email=email, username=username, hashed_password=get_password_hash(DUMMY_PASSWORD))
                db.add(user)
                await db.flush()
            else:
                user.hashed_password = get_password_hash(DUMMY_PASSWORD)

            has_role = (
                await db.execute(select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id))
            ).scalar_one_or_none()
            if has_role is None:
                db.add(UserRole(user_id=user.id, role_id=role.id))

            doctor = (
                await db.execute(select(Doctor).where(Doctor.license_number == spec["license_number"]))
            ).scalar_one_or_none()
            if doctor is None:
                doctor = Doctor(
                    user_id=user.id, first_name=spec["first_name"], last_name=spec["last_name"], email=email,
                    phone="555-100-2000", specialization=spec["specialization"], license_number=spec["license_number"],
                    years_of_experience=spec["years_of_experience"], bio=spec["bio"],
                    certifications="Board Certified", languages_spoken="English", ratings=5,
                    department=spec["department"],
                )
                db.add(doctor)
                await db.flush()
                created += 1
            else:
                doctor.user_id = user.id
                updated += 1
            print(f"{'created' if is_new_user else 'updated'}: {username} -> Doctor #{doctor.id} "
                  f"({spec['specialization']}) password={DUMMY_PASSWORD}")

            has_availability = (
                await db.execute(
                    select(DoctorAvailability.id).where(DoctorAvailability.doctor_id == doctor.id).limit(1)
                )
            ).scalar_one_or_none()
            if has_availability is None:
                for weekday in WEEKDAYS_MON_TO_FRI:
                    db.add(DoctorAvailability(
                        doctor_id=doctor.id, weekday=weekday, start_time="09:00", end_time="17:00", slot_minutes=30,
                    ))
                await db.flush()
                slots = await _generate_slots(db, doctor.id, DAYS_AHEAD)
                print(f"  -> Mon-Fri 9-5 added, {len(slots)} slot(s) generated")

            if spec["external_doctor_id"] is not None and admin_user is not None:
                link = (
                    await db.execute(
                        select(ProviderDirectoryLink).where(
                            ProviderDirectoryLink.source_system == "provider_directory_mock",
                            ProviderDirectoryLink.external_doctor_id == spec["external_doctor_id"],
                        )
                    )
                ).scalar_one_or_none()
                if link is None:
                    db.add(ProviderDirectoryLink(
                        source_system="provider_directory_mock", external_doctor_id=spec["external_doctor_id"],
                        doctor_id=doctor.id, created_by_user_id=admin_user.id,
                    ))
                    print(f"  -> linked to mock provider_directory candidate #{spec['external_doctor_id']}")
                else:
                    link.doctor_id = doctor.id
                    print(f"  -> re-linked mock provider_directory candidate #{spec['external_doctor_id']} "
                          f"(was pointing elsewhere)")

        await db.commit()
        print(f"\nDone: {created} new doctor(s), {updated} already existed (refreshed). "
              f"All 10 share the password '{DUMMY_PASSWORD}'.")
        if admin_user is None:
            print("Note: no admin user found — provider-directory links were skipped. "
                  "Run scripts/grant_role.py <you> admin first, then re-run this script to add them.")


if __name__ == "__main__":
    asyncio.run(main())
