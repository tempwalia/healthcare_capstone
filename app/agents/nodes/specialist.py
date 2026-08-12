from app.agents.state import ReferralState
from app.database import session as db_session
from app.events.outbox import write_outbox_event
from app.models.referral import ReferralRequest, ReferralWorkflowStatus
from app.services.doctor_recommendation import infer_specialty, recommend_from_directory
from app.services.notifications import create_notification_for_role


async def specialist_node(state: ReferralState) -> dict:
    async with db_session.async_session() as db:
        referral = await db.get(ReferralRequest, state["referral_id"])

        ranked = await recommend_from_directory(
            db,
            referral_id=referral.id,
            specialty=infer_specialty(state.get("diagnosis_codes") or [], referral.reason or ""),
            location=referral.preferred_location,
            diagnosis_codes=state.get("diagnosis_codes") or [],
        )

        referral.status = ReferralWorkflowStatus.AWAITING_SPECIALIST_APPROVAL.value
        await write_outbox_event(
            db, "referral.specialist.recommended",
            {"referral_id": referral.id, "candidates": ranked},
            referral_id=referral.id,
        )
        # This is the real "now paused, waiting on a human" moment — unlike
        # eligibility_node's earlier write of this same status, candidates
        # actually exist here. There's no per-referral assigned coordinator
        # in this data model (referral:approve is a role, not an
        # assignment), so every care_coordinator account gets notified.
        await create_notification_for_role(
            db, role_name="care_coordinator",
            title="Referral awaiting specialist approval",
            body=f"Referral #{referral.id} has specialist candidates ready for review.",
            referral_id=referral.id,
        )
        await db.commit()
        return {"specialist_candidates": ranked, "status": referral.status}
