import logging

from sqlalchemy import select

from app.agents.llm import StubChatModel, get_chat_model
from app.agents.nodes.summarizer import format_prior_visits, gather_patient_history
from app.database import session as db_session
from app.models.doctor import Doctor
from app.models.referral import ReferralOutcome, ReferralRequest

logger = logging.getLogger(__name__)

COMPLETION_SUMMARY_PROMPT = (
    "Write a concise care-journey summary for this now-completed referral, useful for whoever handles "
    "this patient's next follow-up. Cover: why they were referred, relevant history, what the consult "
    "found, and what was prescribed/recommended. 4-6 sentences, plain prose, no headers or bullets.\n\n"
    "Patient: {patient_name} (DOB {date_of_birth})\n"
    "Allergies: {allergies}\n"
    "Prior visits: {prior_visits}\n"
    "Referred by: {referring_doctor_name}\n"
    "Reason for referral: {reason}\n"
    "Consult symptoms: {symptoms}\n"
    "Consult diagnosis: {diagnosis}\n"
    "Prescription: {prescription}\n"
    "Follow-up notes: {follow_up_notes}"
)


def _template_completion_summary(context: dict) -> str:
    return (
        f"Referral completed for {context['patient_name']} (DOB {context['date_of_birth']}), "
        f"referred by {context['referring_doctor_name']} for: {context['reason'] or 'no reason provided'}. "
        f"Consult symptoms: {context['symptoms'] or 'not recorded'}. "
        f"Diagnosis: {context['diagnosis'] or 'not recorded'}. "
        f"Prescription: {context['prescription'] or 'none'}. "
        f"Follow-up notes: {context['follow_up_notes'] or 'none'}. "
        f"Allergies: {context['allergies']}. Prior visits: {context['prior_visits']}."
    )


async def generate_completion_summary(referral_id: int) -> None:
    """Runs as a FastAPI BackgroundTask off `POST /requests/{id}/outcome` —
    not a LangGraph node, since it's triggered by a plain REST write (the
    referral's workflow thread already ended at `notify -> END` once
    scheduled) and needs no multi-step orchestration. Reuses
    `gather_patient_history` from Phase 7's summarizer for prior-visit
    context, folding in the newly recorded consult outcome, so the resulting
    `interaction_summary` covers the whole care journey for the next
    follow-up.
    """
    async with db_session.async_session() as db:
        outcome = (
            await db.execute(
                select(ReferralOutcome).where(ReferralOutcome.referral_request_id == referral_id)
            )
        ).scalar_one_or_none()
        if outcome is None:
            logger.warning("completion_summary: no outcome recorded for referral %s, skipping", referral_id)
            return

        referral = await db.get(ReferralRequest, referral_id)
        referring_doctor = await db.get(Doctor, referral.referring_doctor_id)
        history = await gather_patient_history(db, referral.patient_id)

        context = {
            **{k: v for k, v in history.items() if k != "prior_visits"},
            "prior_visits": format_prior_visits(history["prior_visits"]),
            "referring_doctor_name": f"Dr. {referring_doctor.first_name} {referring_doctor.last_name}",
            "reason": referral.reason,
            "symptoms": outcome.symptoms,
            "diagnosis": outcome.diagnosis,
            "prescription": outcome.prescription,
            "follow_up_notes": outcome.follow_up_notes,
        }

        llm = get_chat_model("completion_summary")
        if isinstance(llm, StubChatModel):
            summary = _template_completion_summary(context)
        else:
            try:
                response = await llm.ainvoke(COMPLETION_SUMMARY_PROMPT.format(**context))
                summary = response.content
            except Exception:
                logger.warning("completion_summary: LLM summary failed, falling back to template", exc_info=True)
                summary = _template_completion_summary(context)

        outcome.interaction_summary = summary
        await db.commit()
