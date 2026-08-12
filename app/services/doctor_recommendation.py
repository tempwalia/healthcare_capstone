"""Specialist/doctor recommendation — extracted from
`app.agents.nodes.specialist` so the same specialty-inference and ranking
logic is a plain, reusable service rather than something only a LangGraph
node can call. `specialist_node` is now a thin wrapper around
`recommend_from_directory` (unchanged behavior, external mock provider
directory). `recommend_platform_doctors` is the second candidate source this
enables: our own bookable `doctors` table, ranked by the exact same
`rule_based_rank`/`llm_rank_candidates` — used by direct appointment booking
(no referral involved) via `GET /doctors/recommend`.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import mcp_clients
from app.agents.audit import call_tool_audited
from app.agents.llm import StubChatModel, get_chat_model
from app.models.doctor import Doctor
from app.models.insurance import DoctorInsuranceNetwork, InsurancePlan
from app.models.patient import Patient
from app.models.schedule import ScheduleSlot

# ICD-10 chapter prefixes covering the mock provider directory's three
# specialties. diagnosis_codes only arrives populated once real extraction
# runs; the keyword fallback below covers the common case.
_ICD10_SPECIALTY_PREFIXES = {"M": "Orthopedics", "I": "Cardiology", "L": "Dermatology"}
_SPECIALTY_KEYWORDS = {
    "Orthopedics": ("back", "spine", "joint", "knee", "hip", "shoulder", "fracture", "orthopedic"),
    "Cardiology": ("heart", "cardiac", "chest pain", "palpitation", "cardio"),
    "Dermatology": ("skin", "rash", "derma", "mole", "eczema"),
}


def infer_specialty(diagnosis_codes: List[str], reason: str = "") -> str:
    """Diagnosis-code prefix first (real once diagnosis_codes is populated),
    keyword match against free-text `reason` otherwise."""
    for code in diagnosis_codes or []:
        specialty = _ICD10_SPECIALTY_PREFIXES.get(code[:1].upper())
        if specialty:
            return specialty

    text = (reason or "").lower()
    for specialty, keywords in _SPECIALTY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return specialty

    return "Orthopedics"


def explain(candidate: Dict[str, Any]) -> List[str]:
    parts = ["in-network" if candidate.get("in_network") else "out-of-network"]
    if candidate.get("distance_mi") is not None:
        parts.append("same city as patient" if candidate["distance_mi"] == 0 else f"{candidate['distance_mi']}mi away")
    parts.append(f"{candidate.get('rating')} rating")
    next_days = candidate.get("next_available_days")
    parts.append(f"next slot in {next_days} days" if next_days is not None else "availability unknown")
    return parts


def rule_based_rank(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic fallback — also a legitimate baseline ranking even when
    an LLM is available: weighted in-network/distance/rating/next-slot.
    `distance_mi` may be absent (platform-doctor candidates have no
    meaningful distance today) — `.get(..., 10)` treats that as a neutral
    "unknown" value rather than crashing."""

    def score(c: Dict[str, Any]) -> float:
        s = 0.0
        s += 0.4 if c.get("in_network") else 0
        s += max(0.0, 0.3 - 0.03 * c.get("distance_mi", 10))
        s += 0.2 * (c.get("rating", 0) / 5)
        s += 0.1 if c.get("next_available_days", 30) is not None and c.get("next_available_days", 30) <= 7 else 0
        return round(s, 2)

    ranked = sorted(candidates, key=score, reverse=True)
    for c in ranked:
        c["score"] = score(c)
        c["reasons"] = explain(c)
    return ranked


class _RankedCandidate(BaseModel):
    doctor_id: int
    score: float = Field(ge=0, le=1)
    reasons: List[str]


class _RankedCandidates(BaseModel):
    ranked: List[_RankedCandidate]


_RANKING_PROMPT = (
    "Rank these specialist candidates for a referral with diagnosis codes {diagnosis_codes}. "
    "Favor in-network status, shorter distance, higher rating, and sooner availability. "
    "Return every candidate's doctor_id, a score from 0 to 1, and 2-4 short human-readable reasons.\n\n"
    "Candidates:\n{candidates}"
)


async def llm_rank_candidates(
    llm, candidates: List[Dict[str, Any]], diagnosis_codes: List[str]
) -> List[Dict[str, Any]]:
    """Falls back to `rule_based_rank` whenever the LLM call fails or its
    response doesn't map back onto real candidates — never a 500 just
    because ranking couldn't complete."""
    try:
        result = await llm.with_structured_output(_RankedCandidates).ainvoke(
            _RANKING_PROMPT.format(
                diagnosis_codes=diagnosis_codes or ["unspecified"], candidates=candidates
            )
        )
    except Exception:
        return rule_based_rank(candidates)

    by_id = {c["doctor_id"]: c for c in candidates}
    ranked = [
        {**by_id[rc.doctor_id], "score": rc.score, "reasons": rc.reasons}
        for rc in result.ranked
        if rc.doctor_id in by_id
    ]
    return ranked or rule_based_rank(candidates)


async def rank_candidates(candidates: List[Dict[str, Any]], diagnosis_codes: List[str]) -> List[Dict[str, Any]]:
    """Single entry point for "rank whatever candidates you found" — picks
    LLM vs rule-based the same way every call site should, so that decision
    lives in one place instead of being copy-pasted at each caller."""
    llm = get_chat_model("specialist_ranking")
    if isinstance(llm, StubChatModel):
        return rule_based_rank(candidates)
    return await llm_rank_candidates(llm, candidates, diagnosis_codes)


async def recommend_from_directory(
    db: AsyncSession, *, referral_id: int, specialty: str, location: Optional[str], diagnosis_codes: List[str]
) -> List[Dict[str, Any]]:
    """The referral workflow's existing path: search the external mock
    provider directory (synthetic doctor_ids — see
    app/models/provider_directory_link.py for how those map onto real
    platform doctors), then rank. Audited via `referral_id` since this is
    always reached from within a referral's own workflow."""
    tools = await mcp_clients.get_tools(mcp_clients.DIRECTORY_SERVERS, ["search_providers"])
    candidates = await call_tool_audited(
        db, referral_id=referral_id, tool=tools["search_providers"],
        args={
            "specialty": specialty,
            "location": location,
            # No numeric insurance-plan id exists on Patient/ReferralRequest
            # in this schema (only free-text provider/policy strings) — the
            # mock directory treats every candidate as out-of-network
            # without one, which is the honest behavior given the data we
            # actually have, not a bug to paper over.
            "insurance_plan_id": None,
        },
    )
    return await rank_candidates(candidates, diagnosis_codes)


async def recommend_platform_doctors(
    db: AsyncSession, *, specialty: str, patient_id: Optional[int] = None,
    diagnosis_codes: Optional[List[str]] = None, limit: int = 10,
) -> List[Dict[str, Any]]:
    """Real, bookable doctors on this platform, shaped into the exact same
    candidate dict `rule_based_rank`/`llm_rank_candidates` already expects —
    so direct appointment booking (no referral) gets a "recommend a doctor
    for me" step powered by the identical ranking logic the referral
    workflow uses, just sourced from our own `doctors`/`schedule_slots`
    tables instead of the external mock directory. Falls back to every
    active doctor if nothing matches the inferred specialty, since an empty
    recommendation list isn't useful to anyone."""
    doctors = (
        await db.execute(
            select(Doctor).where(Doctor.deleted_at.is_(None), Doctor.specialization.ilike(f"%{specialty}%"))
        )
    ).scalars().all()
    if not doctors:
        doctors = (await db.execute(select(Doctor).where(Doctor.deleted_at.is_(None)))).scalars().all()

    patient = await db.get(Patient, patient_id) if patient_id is not None else None

    in_network_doctor_ids: set = set()
    if patient is not None and patient.insurance_provider:
        rows = await db.execute(
            select(DoctorInsuranceNetwork.doctor_id)
            .join(InsurancePlan, InsurancePlan.id == DoctorInsuranceNetwork.insurance_plan_id)
            .where(InsurancePlan.provider.ilike(f"%{patient.insurance_provider}%"))
        )
        in_network_doctor_ids = set(rows.scalars().all())

    # Simple city/region text match, not lat/long — see Doctor.city/
    # Patient.city. Case-insensitive; blank on either side leaves
    # distance_mi unset below so rule_based_rank's neutral default applies.
    patient_city = (patient.city or "").strip().lower() if patient is not None else ""

    now = datetime.now(timezone.utc)
    candidates = []
    for doctor in doctors[:limit]:
        next_slot_at = (
            await db.execute(
                select(ScheduleSlot.starts_at)
                .where(
                    ScheduleSlot.doctor_id == doctor.id,
                    ScheduleSlot.is_booked.is_(False),
                    ScheduleSlot.deleted_at.is_(None),
                    ScheduleSlot.starts_at > now,
                )
                .order_by(ScheduleSlot.starts_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        candidate = {
            "doctor_id": doctor.id,
            "first_name": doctor.first_name,
            "last_name": doctor.last_name,
            "specialization": doctor.specialization,
            "in_network": doctor.id in in_network_doctor_ids,
            "rating": doctor.ratings or 0,
            "next_available_days": (next_slot_at - now).days if next_slot_at else None,
        }
        doctor_city = (doctor.city or "").strip().lower()
        if patient_city and doctor_city and patient_city == doctor_city:
            candidate["distance_mi"] = 0
        candidates.append(candidate)

    return await rank_candidates(candidates, diagnosis_codes or [])
