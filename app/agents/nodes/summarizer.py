import logging
from typing import Any, Dict, List

from sqlalchemy import select

from app.agents.llm import StubChatModel, get_chat_model
from app.agents.state import ReferralState
from app.database import session as db_session
from app.models.medical_record import MedicalRecord
from app.models.patient import Patient
from app.models.referral import ReferralRequest, SpecialistNote

logger = logging.getLogger(__name__)

PRIOR_VISITS_LIMIT = 5

SUMMARY_PROMPT = (
    "Write a brief, clinically useful referral-history summary for the specialist about to see this "
    "patient. Cover: why they're being referred now, relevant allergies, and any relevant prior "
    "diagnoses/treatments. 3-5 sentences, plain prose, no headers or bullet points.\n\n"
    "Patient: {patient_name} (DOB {date_of_birth})\n"
    "Reason for this referral: {reason}\n"
    "Allergies: {allergies}\n"
    "Prior visits: {prior_visits}"
)


async def gather_patient_history(db, patient_id: int) -> Dict[str, Any]:
    patient = await db.get(Patient, patient_id)
    records = (
        await db.execute(
            select(MedicalRecord)
            .where(MedicalRecord.patient_id == patient_id, MedicalRecord.deleted_at.is_(None))
            .order_by(MedicalRecord.visit_date.desc())
            .limit(PRIOR_VISITS_LIMIT)
        )
    ).scalars().all()

    return {
        "patient_name": f"{patient.first_name} {patient.last_name}",
        "date_of_birth": str(patient.date_of_birth),
        "allergies": patient.allergies or "none recorded",
        "prior_visits": [
            {"date": str(r.visit_date), "diagnosis": r.diagnosis, "treatment": r.treatment}
            for r in records
        ],
    }


def format_prior_visits(prior_visits: List[Dict[str, Any]]) -> str:
    if not prior_visits:
        return "none on file"
    return "; ".join(
        f"{v['date']}: {v['diagnosis'] or 'no diagnosis recorded'}" for v in prior_visits
    )


def template_summary(history: Dict[str, Any], reason: str) -> str:
    return (
        f"Referral for {history['patient_name']} (DOB {history['date_of_birth']}): "
        f"{reason or 'no reason provided'}. Allergies: {history['allergies']}. "
        f"Prior visits: {format_prior_visits(history['prior_visits'])}."
    )


async def summarizer_node(state: ReferralState) -> dict:
    async with db_session.async_session() as db:
        referral = await db.get(ReferralRequest, state["referral_id"])
        history = await gather_patient_history(db, state["patient_id"])
        reason = referral.reason or ""

        llm = get_chat_model("summarization")
        if isinstance(llm, StubChatModel):
            summary = template_summary(history, reason)
        else:
            try:
                response = await llm.ainvoke(
                    SUMMARY_PROMPT.format(
                        reason=reason or "not specified",
                        prior_visits=format_prior_visits(history["prior_visits"]),
                        **{k: v for k, v in history.items() if k != "prior_visits"},
                    )
                )
                summary = response.content
            except Exception:
                logger.warning("summarizer: LLM summary failed, falling back to template", exc_info=True)
                summary = template_summary(history, reason)

        db.add(SpecialistNote(referral_request_id=referral.id, note=summary))
        await db.commit()

    return {}
