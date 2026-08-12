"""Dev/manual-testing utility: gives every doctor that has zero
DoctorAvailability rows a default Mon-Fri 9:00-17:00 weekly schedule
(30-minute slots), then generates concrete bookable ScheduleSlot rows from
it for the next 14 days.

Without this, a freshly created doctor has no availability at all, so
"View Available Slots" on the Scheduling page's recommended-doctor flow
always dead-ends with "No open slots for this doctor yet" — the exact
symptom reported for a demo account. Doctors that already have availability
configured are left untouched (their own schedule wins, this only fills
the gap for doctors nobody ever configured).

    uv run python scripts/seed_doctor_availability.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.api.routes.schedule import _generate_slots  # noqa: E402
from app.database.session import async_session  # noqa: E402
from app.models.doctor import Doctor  # noqa: E402
from app.models.schedule import DoctorAvailability  # noqa: E402

WEEKDAYS_MON_TO_FRI = range(5)  # 0=Monday .. 4=Friday
DAYS_AHEAD = 14


async def main() -> None:
    async with async_session() as db:
        doctors = (await db.execute(select(Doctor).where(Doctor.deleted_at.is_(None)))).scalars().all()
        if not doctors:
            print("No doctors found — create some first.")
            return

        seeded, skipped = 0, 0
        for doctor in doctors:
            has_availability = (
                await db.execute(
                    select(DoctorAvailability.id).where(DoctorAvailability.doctor_id == doctor.id).limit(1)
                )
            ).scalar_one_or_none()
            if has_availability is not None:
                skipped += 1
                continue

            for weekday in WEEKDAYS_MON_TO_FRI:
                db.add(DoctorAvailability(
                    doctor_id=doctor.id, weekday=weekday,
                    start_time="09:00", end_time="17:00", slot_minutes=30,
                ))
            await db.flush()

            created = await _generate_slots(db, doctor.id, DAYS_AHEAD)
            seeded += 1
            print(f"#{doctor.id:<4} {doctor.first_name} {doctor.last_name:<20} ({doctor.specialization}) "
                  f"-> Mon-Fri 9-5 added, {len(created)} slot(s) generated")

        await db.commit()
        print(f"\nSeeded availability for {seeded} doctor(s); {skipped} already had their own.")


if __name__ == "__main__":
    asyncio.run(main())
