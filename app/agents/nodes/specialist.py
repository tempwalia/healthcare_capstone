from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.agents import mcp_clients
from app.agents.audit import call_tool_audited
from app.agents.llm import StubChatModel, get_chat_model
from app.agents.state import ReferralState
from app.database import session as db_session
from app.events.outbox import write_outbox_event
from app.models.referral import ReferralRequest, ReferralWorkflowStatus
from app.services.notifications import create_notification_for_role

# ICD-10 chapter prefixes covering the mock provider directory's three
# specialties. diagnosis_codes only arrives populated once Phase 7's real
# extraction lands; the keyword fallback below covers Phase 6.
_ICD10_SPECIALTY_PREFIXES = {"M": "Orthopedics", "I": "Cardiology", "L": "Dermatology"}
_SPECIALTY_KEYWORDS = {
    "Orthopedics": ("back", "spine", "joint", "knee", "hip", "shoulder", "fracture", "orthopedic"),
    "Cardiology": ("heart", "cardiac", "chest pain", "palpitation", "cardio"),
    "Dermatology": ("skin", "rash", "derma", "mole", "eczema"),
}


def infer_specialty(diagnosis_codes: List[str], reason: str = "") -> str:
    """Diagnosis-code prefix first (real once diagnosis_codes is populated),
    keyword match against the referral's free-text `reason` otherwise —
    `reason` is submitted with the referral itself, so unlike uploaded
    documents it's always available the moment this node runs."""
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
    next_days = candidate.get("next_available_days")
    return [
        "in-network" if candidate.get("in_network") else "out-of-network",
        f"{candidate.get('distance_mi')}mi away",
        f"{candidate.get('rating')} rating",
        f"next slot in {next_days} days" if next_days is not None else "availability unknown",
    ]


def rule_based_rank(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """ADR-005's deterministic fallback — also a legitimate baseline ranking
    even when an LLM is available: weighted in-network/distance/rating/next-slot."""

    def score(c: Dict[str, Any]) -> float:
        s = 0.0
        s += 0.4 if c.get("in_network") else 0
        s += max(0.0, 0.3 - 0.03 * c.get("distance_mi", 10))
        s += 0.2 * (c.get("rating", 0) / 5)
        s += 0.1 if c.get("next_available_days", 30) <= 7 else 0
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
    """The guide references this function but never defines it. Falls back to
    `rule_based_rank` whenever the LLM call fails or its response doesn't map
    back onto real candidates — ADR-005's "never a 500" guarantee applies to
    the real-LLM path too, not just the missing-key path."""
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


async def specialist_node(state: ReferralState) -> dict:
    async with db_session.async_session() as db:
        referral = await db.get(ReferralRequest, state["referral_id"])

        tools = await mcp_clients.get_tools(mcp_clients.DIRECTORY_SERVERS, ["search_providers"])
        candidates = await call_tool_audited(
            db, referral_id=referral.id, tool=tools["search_providers"],
            args={
                "specialty": infer_specialty(state.get("diagnosis_codes") or [], referral.reason or ""),
                "location": referral.preferred_location,
                # No numeric insurance-plan id exists on Patient/ReferralRequest
                # in this schema (only free-text provider/policy strings) — the
                # mock directory treats every candidate as out-of-network
                # without one, which is the honest behavior given the data we
                # actually have, not a bug to paper over.
                "insurance_plan_id": None,
            },
        )

        llm = get_chat_model("specialist_ranking")
        if isinstance(llm, StubChatModel):
            ranked = rule_based_rank(candidates)
        else:
            ranked = await llm_rank_candidates(llm, candidates, state.get("diagnosis_codes") or [])

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
