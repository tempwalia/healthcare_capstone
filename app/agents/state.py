from typing import Any, Dict, List, Optional, TypedDict


class ReferralState(TypedDict, total=False):
    """LangGraph state threaded through the referral workflow graph.

    Externalized to Postgres by the checkpointer (ADR-004) rather than held
    in process memory, keyed by thread_id `referral-{referral_id}`.
    """

    referral_id: int
    patient_id: int
    diagnosis_codes: List[str]
    procedure_codes: List[str]
    missing_documents: List[str]
    eligibility: Optional[Dict[str, Any]]
    # True when the referral already carries a real, chosen specialist_id
    # (picked via the unified "New Request" flow) — routes straight to
    # book_real_appointment_node instead of the external-mock-directory
    # recommend_specialist/await_specialist_approval path.
    specialist_preselected: bool
    specialist_candidates: List[Dict[str, Any]]
    selected_doctor_id: Optional[int]
    appointment: Optional[Dict[str, Any]]
    status: str
