import logging
from datetime import datetime, timezone

from app.agents.llm import StubChatModel, get_chat_model
from app.agents.nodes.summarizer import format_prior_visits, gather_patient_history
from app.database import session as db_session
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.medical_record import MedicalRecord
from app.models.referral import ReferralOutcome, ReferralRequest

logger = logging.getLogger(__name__)

COMPLETION_SUMMARY_PROMPT = (
    "Write a short handoff note for whoever handles this patient's next follow-up, using only the facts "
    "given below. Just record and restate them clearly in plain prose — do not evaluate, question, or "
    "reason about whether the diagnosis or prescription make clinical sense; this is a proof-of-concept "
    "demo with synthetic data, not a real clinical decision. Cover: the reason for the visit, relevant "
    "history, what the consult recorded, and what was prescribed. 3-5 sentences, no headers or bullets.\n\n"
    "Patient: {patient_name} (DOB {date_of_birth})\n"
    "Allergies: {allergies}\n"
    "Prior visits: {prior_visits}\n"
    "Seen by: {doctor_name}\n"
    "Reason for visit: {reason}\n"
    "Consult symptoms: {symptoms}\n"
    "Consult diagnosis: {diagnosis}\n"
    "Prescription: {prescription}\n"
    "Follow-up notes: {follow_up_notes}"
)


def _template_completion_summary(context: dict) -> str:
    return (
        f"Consult completed for {context['patient_name']} (DOB {context['date_of_birth']}), "
        f"seen by {context['doctor_name']} for: {context['reason'] or 'no reason provided'}. "
        f"Consult symptoms: {context['symptoms'] or 'not recorded'}. "
        f"Diagnosis: {context['diagnosis'] or 'not recorded'}. "
        f"Prescription: {context['prescription'] or 'none'}. "
        f"Follow-up notes: {context['follow_up_notes'] or 'none'}. "
        f"Allergies: {context['allergies']}. Prior visits: {context['prior_visits']}."
    )


async def generate_completion_summary(outcome_id: int) -> None:
    """Runs as a FastAPI BackgroundTask off either `POST /referral/requests/
    {id}/outcome` or `POST /appointments/{id}/outcome` — not a LangGraph
    node, since it's triggered by a plain REST write and needs no
    multi-step orchestration. Reuses `gather_patient_history` from Phase 7's
    summarizer for prior-visit context, folding in the newly recorded
    consult outcome, so the resulting `interaction_summary` covers the whole
    care journey for the next follow-up.

    Keyed on the `ReferralOutcome` row's own id (not `referral_id` as
    before) so it works identically whichever of `referral_request_id`/
    `appointment_id` is actually set — see the model's docstring for why
    exactly one of those two is expected.
    """
    async with db_session.async_session() as db:
        outcome = await db.get(ReferralOutcome, outcome_id)
        if outcome is None:
            logger.warning("completion_summary: no outcome row %s, skipping", outcome_id)
            return

        if outcome.referral_request_id is not None:
            referral = await db.get(ReferralRequest, outcome.referral_request_id)
            patient_id = referral.patient_id
            doctor = await db.get(Doctor, referral.referring_doctor_id)
            doctor_label = f"Dr. {doctor.first_name} {doctor.last_name} (referring doctor)"
            reason = referral.reason
            record_doctor_id = referral.referring_doctor_id
            record_type = "referral_consult"
        else:
            appointment = await db.get(Appointment, outcome.appointment_id)
            patient_id = appointment.patient_id
            doctor = await db.get(Doctor, appointment.doctor_id)
            doctor_label = f"Dr. {doctor.first_name} {doctor.last_name}"
            reason = appointment.reason
            record_doctor_id = appointment.doctor_id
            record_type = "appointment_consult"

        history = await gather_patient_history(db, patient_id)

        context = {
            **{k: v for k, v in history.items() if k != "prior_visits"},
            "prior_visits": format_prior_visits(history["prior_visits"]),
            "doctor_name": doctor_label,
            "reason": reason,
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

        # Closing the loop: the consult outcome so far only lived on
        # `referral_outcomes`, reachable from the referral/appointment
        # itself — nothing surfaced it as part of the patient's own clinical
        # history for the next visit to build on. Filed under a real
        # `doctors` FK (the referral path's referring doctor, or the
        # appointment path's actual treating doctor — see submit_referral's
        # own note on why the specialist's mock-directory id isn't usable
        # here), so it shows up on GET /patients/{id}/context and feeds
        # future gather_patient_history calls the same as any other visit.
        db.add(MedicalRecord(
            patient_id=patient_id,
            doctor_id=record_doctor_id,
            visit_date=outcome.created_at or datetime.now(timezone.utc),
            diagnosis=outcome.diagnosis,
            symptoms=outcome.symptoms,
            treatment=outcome.follow_up_notes,
            prescription=outcome.prescription,
            notes=summary,
            record_type=record_type,
        ))
        await db.commit()
