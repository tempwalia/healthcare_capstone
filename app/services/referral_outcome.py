import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.agents.llm import StubChatModel, get_chat_model
from app.agents.nodes.summarizer import format_prior_visits, gather_patient_history
from app.database import session as db_session
from app.models.doctor import Doctor
from app.models.medical_record import MedicalRecord
from app.models.referral import ReferralOutcome, ReferralRequest

logger = logging.getLogger(__name__)

COMPLETION_SUMMARY_PROMPT = (
    "Write a short handoff note for whoever handles this patient's next follow-up, using only the facts "
    "given below. Just record and restate them clearly in plain prose — do not evaluate, question, or "
    "reason about whether the diagnosis or prescription make clinical sense; this is a proof-of-concept "
    "demo with synthetic data, not a real clinical decision. Cover: why they were referred, relevant "
    "history, what the consult recorded, and what was prescribed. 3-5 sentences, no headers or bullets.\n\n"
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

        # Closing the referral loop: the consult outcome so far only lived on
        # `referral_outcomes`, reachable from the referral itself — nothing
        # surfaced it as part of the patient's own clinical history for the
        # next visit to build on. Filed under the referring doctor (a real
        # `doctors` FK; the specialist's mock-directory id isn't a row in
        # that table — see submit_referral's own note on that), so it shows
        # up on GET /patients/{id}/context and feeds future gather_patient_history
        # calls the same as any other visit.
        db.add(MedicalRecord(
            patient_id=referral.patient_id,
            doctor_id=referral.referring_doctor_id,
            visit_date=outcome.created_at or datetime.now(timezone.utc),
            diagnosis=outcome.diagnosis,
            symptoms=outcome.symptoms,
            treatment=outcome.follow_up_notes,
            prescription=outcome.prescription,
            notes=summary,
            record_type="referral_consult",
        ))
        await db.commit()
