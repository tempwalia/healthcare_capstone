"""Mock Scheduling system.

Contract: GET /availability?doctor_id=&within_days= -> open slots (generated
on the fly from a fixed 9am-5pm/30min work-day pattern, minus whatever's
already booked). POST /slots/book {doctor_id, slot_id} -> booking confirmation.
Exposed to agents as MCP tools `get_availability` / `book_slot` — the
mandatory "appointment scheduling" step of the referral workflow.

Booking state lives in-memory for the life of the process — fine for a mock,
not meant to survive a restart.
"""
from datetime import datetime, time, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi_mcp import FastApiMCP
from pydantic import BaseModel

app = FastAPI(title="Mock Scheduling System", description="Stand-in for an external scheduling system.")

WORKDAY_START = time(9, 0)
WORKDAY_END = time(17, 0)
SLOT_MINUTES = 30
MAX_SLOTS_RETURNED = 20

booked_slot_ids: set[str] = set()
bookings: dict[int, dict] = {}
_next_appointment_id = 1000


class Slot(BaseModel):
    slot_id: str
    doctor_id: int
    starts_at: datetime
    ends_at: datetime


class BookSlotRequest(BaseModel):
    doctor_id: int
    slot_id: str


class BookingConfirmation(BaseModel):
    appointment_id: int
    status: str
    scheduled_for: datetime


def _generate_slots(doctor_id: int, within_days: int) -> List[Slot]:
    now = datetime.now(timezone.utc)
    slots: List[Slot] = []
    for day_offset in range(1, within_days + 1):
        day = (now + timedelta(days=day_offset)).date()
        current = datetime.combine(day, WORKDAY_START, tzinfo=timezone.utc)
        end_of_day = datetime.combine(day, WORKDAY_END, tzinfo=timezone.utc)
        while current < end_of_day and len(slots) < MAX_SLOTS_RETURNED:
            slot_id = f"{doctor_id}-{current.strftime('%Y%m%dT%H%M')}"
            if slot_id not in booked_slot_ids:
                slots.append(
                    Slot(
                        slot_id=slot_id,
                        doctor_id=doctor_id,
                        starts_at=current,
                        ends_at=current + timedelta(minutes=SLOT_MINUTES),
                    )
                )
            current += timedelta(minutes=SLOT_MINUTES)
        if len(slots) >= MAX_SLOTS_RETURNED:
            break
    return slots


@app.get("/availability", response_model=List[Slot], operation_id="get_availability")
async def get_availability(doctor_id: int, within_days: int = 14):
    return _generate_slots(doctor_id, within_days)


@app.post("/slots/book", response_model=BookingConfirmation, operation_id="book_slot")
async def book_slot(body: BookSlotRequest):
    global _next_appointment_id

    if body.slot_id in booked_slot_ids:
        raise HTTPException(409, "Slot already booked")
    if not body.slot_id.startswith(f"{body.doctor_id}-"):
        raise HTTPException(400, "slot_id does not belong to doctor_id")

    try:
        timestamp_part = body.slot_id.split("-", 1)[1]
        starts_at = datetime.strptime(timestamp_part, "%Y%m%dT%H%M").replace(tzinfo=timezone.utc)
    except (IndexError, ValueError):
        raise HTTPException(400, "Malformed slot_id")

    booked_slot_ids.add(body.slot_id)
    appointment_id = _next_appointment_id
    _next_appointment_id += 1
    bookings[appointment_id] = {"doctor_id": body.doctor_id, "slot_id": body.slot_id, "starts_at": starts_at}

    return BookingConfirmation(appointment_id=appointment_id, status="confirmed", scheduled_for=starts_at)


mcp = FastApiMCP(app)
mcp.mount_http()
