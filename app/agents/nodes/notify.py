from app.agents import mcp_clients
from app.agents.audit import call_tool_audited
from app.agents.state import ReferralState
from app.database import session as db_session
from app.models.patient import Patient
from app.models.referral import ReferralRequest
from app.services.audit import log_action
from app.services.notifications import create_notification


async def notify_node(state: ReferralState) -> dict:
    async with db_session.async_session() as db:
        referral = await db.get(ReferralRequest, state["referral_id"])
        patient = await db.get(Patient, referral.patient_id)

        if patient is None or patient.user_id is None:
            # No linked platform login to notify — a real patient/PCP intake
            # gap, not something to crash the workflow over.
            await log_action(
                db, actor_id=None, action="referral.notification.skipped",
                resource_type="referral_request", resource_id=referral.id,
                details={"reason": "patient has no linked user account"},
            )
            await db.commit()
            return {}

        tools = await mcp_clients.get_tools(mcp_clients.NOTIFICATION_SERVERS, ["send_notification"])
        appointment = state.get("appointment") or {}
        scheduled_for = appointment.get("scheduled_for", "a time to be confirmed")
        message = f"Your referral appointment is confirmed for {scheduled_for}."
        await call_tool_audited(
            db, referral_id=referral.id, tool=tools["send_notification"],
            args={"user_id": patient.user_id, "channel": "email", "message": message},
        )
        # Real, in-app, persisted notification the dashboard's bell icon
        # actually reads — separate from the call above, which only reaches
        # the mock external email/SMS/push provider and persists nothing.
        await create_notification(
            db, user_id=patient.user_id, title="Referral appointment scheduled",
            body=message, referral_id=referral.id,
        )
        await db.commit()

    return {}
