"""Dev/manual-testing utility: assigns a realistic insurance policy to every
patient in the database — newly created ones and existing ones alike — so
that submitting a referral for any patient produces varied, realistic
eligibility outcomes instead of everyone needing the same two hardcoded
policy numbers typed in by hand.

Eligibility is driven entirely by `Patient.insurance_policy_number` matching
a key in `mock_systems/payer_mock/main.py::PLANS` (a plain string match —
there is no numeric insurance_plan_id FK wired into the real referral
workflow; see specialist_node's own comment on that, and note the separate
`insurance_plans`/`doctor_insurance_networks` DB tables are unused dead
schema today, not touched by this script or by eligibility checking at all).

Most patients (roughly 75%) get one of the four verified demo policies, so
their referrals sail through eligibility. The rest get an unrecognized
policy number on purpose, so the eligibility_denied path has real patients
to test against too — not just the happy path.

    uv run python scripts/seed_sample_insurance.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database.session import async_session  # noqa: E402
from app.models.patient import Patient  # noqa: E402
from app.services.insurance import VERIFIED_POLICIES, random_policy  # noqa: E402


async def main() -> None:
    async with async_session() as db:
        patients = (
            await db.execute(select(Patient).where(Patient.deleted_at.is_(None)))
        ).scalars().all()

        if not patients:
            print("No patients found — create some first.")
            return

        for patient in patients:
            policy_number, provider = random_policy()
            outcome = "eligible" if policy_number in VERIFIED_POLICIES else "NOT eligible (denial-path test case)"

            patient.insurance_provider = provider
            patient.insurance_policy_number = policy_number
            print(f"#{patient.id:<4} {patient.first_name} {patient.last_name:<20} -> {policy_number:<20} ({provider}) — {outcome}")

        await db.commit()
        print(f"\nUpdated {len(patients)} patient(s).")


if __name__ == "__main__":
    asyncio.run(main())
