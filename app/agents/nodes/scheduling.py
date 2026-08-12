from datetime import datetime

from app.agents import mcp_clients
from app.agents.audit import call_tool_audited
from app.agents.state import ReferralState
from app.database import session as db_session
from app.events.outbox import write_outbox_event
from app.models.appointment import Appointment, AppointmentStatusEnum
from app.models.referral import ReferralRequest, ReferralWorkflowStatus


async def scheduling_node(state: ReferralState) -> dict:
    async with db_session.async_session() as db:
        referral = await db.get(ReferralRequest, state["referral_id"])
        tools = await mcp_clients.get_tools(
            mcp_clients.SCHEDULING_SERVERS, ["get_availability", "book_slot"]
        )

        slots = await call_tool_audited(
            db, referral_id=referral.id, tool=tools["get_availability"],
            args={"doctor_id": state["selected_doctor_id"], "within_days": 14},
        )
        if not slots:
            referral.status = "scheduling_delayed"
            await write_outbox_event(
                db, "referral.delay.predicted",
                {"referral_id": referral.id, "reason": "no_slots_within_target"},
                referral_id=referral.id,
            )
            await db.commit()
            return {"appointment": None, "status": referral.status}

        booking = await call_tool_audited(
            db, referral_id=referral.id, tool=tools["book_slot"],
            args={"doctor_id": state["selected_doctor_id"], "slot_id": slots[0]["slot_id"]},
        )

        # Not `referral.specialist_id = state["selected_doctor_id"]`: that
        # column is a real FK into our own `doctors` table, but the mock
        # provider directory's doctor_id (88, 91, ...) is a separate,
        # synthetic ID space representing external specialists with no row
        # there at all — SQLite let this slide in tests (no FK enforcement
        # by default), real Postgres correctly rejected it. `specialist_id`
        # only gets a real value via an optional coordinator-vouched-for
        # ProviderDirectoryLink mapping (app/api/routes/ai/referral_workflow.py's
        # resume_workflow) — check for that here, not `selected_doctor_id`.
        referral.status = ReferralWorkflowStatus.SCHEDULED.value

        if referral.specialist_id is not None:
            # A real Appointment row only exists once a mapping made
            # `specialist_id` FK-safe — booking["scheduled_for"] crosses the
            # MCP/HTTP boundary as an ISO string, not a Python datetime.
            db.add(Appointment(
                patient_id=referral.patient_id,
                doctor_id=referral.specialist_id,
                appointment_datetime=datetime.fromisoformat(booking["scheduled_for"]),
                status=AppointmentStatusEnum.SCHEDULED,
                reason=referral.reason,
                appointment_type="referral_consult",
                referral_id=referral.id,
            ))

        await write_outbox_event(
            db, "referral.appointment.scheduled",
            {"referral_id": referral.id, **booking},
            referral_id=referral.id,
        )
        await db.commit()
        return {"appointment": booking, "status": referral.status}
