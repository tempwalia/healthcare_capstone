from langgraph.types import interrupt

from app.agents.state import ReferralState
from app.database import session as db_session
from app.events.outbox import write_outbox_event
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.referral import ReferralRequest, ReferralWorkflowStatus
from app.services.audit import log_action
from app.services.eligibility import check_eligibility
from app.services.notifications import create_notification


async def eligibility_node(state: ReferralState) -> dict:
    async with db_session.async_session() as db:
        referral = await db.get(ReferralRequest, state["referral_id"])
        patient = await db.get(Patient, referral.patient_id)

        result = await check_eligibility(
            db, referral_id=referral.id,
            insurance_policy_number=patient.insurance_policy_number,
            procedure_code=(state.get("diagnosis_codes") or ["UNKNOWN"])[0],
        )

        referral.status = (
            ReferralWorkflowStatus.ELIGIBILITY_DENIED.value if not result["verified"]
            else ReferralWorkflowStatus.AWAITING_SPECIALIST_APPROVAL.value
        )
        await write_outbox_event(
            db,
            "referral.eligibility.verified" if result["verified"] else "referral.eligibility.denied",
            {"referral_id": referral.id, **result},
            referral_id=referral.id,
        )

        if not result["verified"]:
            referring_doctor = await db.get(Doctor, referral.referring_doctor_id)
            if referring_doctor is not None and referring_doctor.user_id is not None:
                await create_notification(
                    db, user_id=referring_doctor.user_id, title="Referral eligibility check failed",
                    body=f"Insurance eligibility could not be verified for referral #{referral.id}.",
                    referral_id=referral.id,
                )
            else:
                # Same best-effort-skip-with-audit-log convention as
                # notify_node's patient-side case (record_referral_outcome's
                # equivalent skip in referral.py stays silent for now).
                await log_action(
                    db, actor_id=None, action="referral.notification.skipped",
                    resource_type="referral_request", resource_id=referral.id,
                    details={"reason": "referring doctor has no linked user account", "event": "eligibility_denied"},
                )

        await db.commit()
        return {
            "eligibility": result,
            "status": referral.status,
            "specialist_preselected": referral.specialist_id is not None,
        }


async def escalate_eligibility_node(state: ReferralState) -> dict:
    """Reached when `eligibility_node` denies coverage — the referral is
    already `eligibility_denied`. Gets a human's attention, then genuinely
    pauses here (same `interrupt()` pattern as `await_specialist_approval`)
    instead of dead-ending the graph run: a care coordinator reviews the
    denial (comments, optionally attaches a document — see
    POST /referral/requests/{id}/notes and the existing document upload
    route) and either resolves it themselves or resumes via
    POST /referral-workflow/{id}/override-eligibility, which continues on to
    `recommend_specialist` — the exact same modular step a normally-eligible
    referral goes through, not a separate parallel path."""
    async with db_session.async_session() as db:
        await log_action(
            db, actor_id=None, action="referral.eligibility.escalated",
            resource_type="referral_request", resource_id=state["referral_id"],
        )
        await write_outbox_event(
            db, "referral.eligibility.escalated",
            {"referral_id": state["referral_id"], "eligibility": state.get("eligibility")},
            referral_id=state["referral_id"],
        )
        await db.commit()

    interrupt({"referral_id": state["referral_id"], "eligibility": state.get("eligibility"), "reason": "eligibility_denied"})
    return {"status": ReferralWorkflowStatus.AWAITING_SPECIALIST_APPROVAL.value}
