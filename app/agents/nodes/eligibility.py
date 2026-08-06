from app.agents import mcp_clients
from app.agents.audit import call_tool_audited
from app.agents.state import ReferralState
from app.database import session as db_session
from app.events.outbox import write_outbox_event
from app.models.patient import Patient
from app.models.referral import ReferralRequest, ReferralWorkflowStatus
from app.services.audit import log_action


async def eligibility_node(state: ReferralState) -> dict:
    async with db_session.async_session() as db:
        referral = await db.get(ReferralRequest, state["referral_id"])
        patient = await db.get(Patient, referral.patient_id)

        tools = await mcp_clients.get_tools(mcp_clients.ELIGIBILITY_SERVERS, ["check_eligibility"])
        result = await call_tool_audited(
            db, referral_id=referral.id, tool=tools["check_eligibility"],
            args={
                "insurance_policy_number": patient.insurance_policy_number or "",
                "procedure_code": (state.get("diagnosis_codes") or ["UNKNOWN"])[0],
            },
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
        await db.commit()
        return {"eligibility": result, "status": referral.status}


async def escalate_eligibility_node(state: ReferralState) -> dict:
    """Reached when `eligibility_node` denies coverage — the referral is
    already `eligibility_denied`; this node's job is just to get a human's
    attention. Resuming this path (a coordinator override endpoint) is out
    of Phase 6's scope, so the graph run simply ends here."""
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

    return {"status": ReferralWorkflowStatus.ELIGIBILITY_DENIED.value}
