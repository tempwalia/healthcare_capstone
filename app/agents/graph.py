from typing import Any, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.nodes.eligibility import eligibility_node, escalate_eligibility_node
from app.agents.nodes.intake import intake_node
from app.agents.nodes.notify import notify_node
from app.agents.nodes.scheduling import book_real_appointment_node, scheduling_node
from app.agents.nodes.specialist import specialist_node
from app.agents.nodes.summarizer import summarizer_node
from app.agents.state import ReferralState


def route_after_intake(state: ReferralState) -> str:
    return "await_documents" if state.get("missing_documents") else "verify_eligibility"


def route_after_eligibility(state: ReferralState) -> str:
    if not state["eligibility"]["verified"]:
        return "escalate_eligibility"
    return "book_real_appointment" if state.get("specialist_preselected") else "recommend_specialist"


def route_after_escalation(state: ReferralState) -> str:
    """Same "already have a real, chosen doctor" branch as
    route_after_eligibility — an overridden-eligibility referral with a
    pre-selected specialist also skips straight to booking, not the
    external-mock-directory recommendation step."""
    return "book_real_appointment" if state.get("specialist_preselected") else "recommend_specialist"


async def await_documents_node(state: ReferralState) -> dict:
    """Reached only once Phase 7 makes `intake_node` populate
    `missing_documents` for real. The graph run ends here — resuming via a
    fresh document upload isn't built in Phase 6."""
    return {"status": "awaiting_documents"}


async def await_specialist_approval(state: ReferralState) -> dict:
    decision = interrupt({"candidates": state["specialist_candidates"], "referral_id": state["referral_id"]})
    return {"selected_doctor_id": decision["doctor_id"], "status": "scheduling"}


def build_graph(checkpointer) -> Any:
    g = StateGraph(ReferralState)
    g.add_node("intake", intake_node)
    g.add_node("await_documents", await_documents_node)
    g.add_node("verify_eligibility", eligibility_node)
    g.add_node("escalate_eligibility", escalate_eligibility_node)
    g.add_node("recommend_specialist", specialist_node)
    g.add_node("await_specialist_approval", await_specialist_approval)
    g.add_node("schedule_appointment", scheduling_node)
    g.add_node("book_real_appointment", book_real_appointment_node)
    g.add_node("summarize_for_specialist", summarizer_node)
    g.add_node("notify", notify_node)

    g.add_edge(START, "intake")
    g.add_conditional_edges(
        "intake", route_after_intake,
        {"await_documents": "await_documents", "verify_eligibility": "verify_eligibility"},
    )
    g.add_edge("await_documents", END)  # resumes via a fresh submit once docs arrive
    g.add_conditional_edges(
        "verify_eligibility", route_after_eligibility,
        {
            "escalate_eligibility": "escalate_eligibility",
            "recommend_specialist": "recommend_specialist",
            "book_real_appointment": "book_real_appointment",
        },
    )
    # Pauses (interrupt()) rather than dead-ending — resumes via
    # POST /referral-workflow/{id}/override-eligibility into either the same
    # recommend_specialist step a normally-eligible referral reaches, or
    # straight to book_real_appointment if a real specialist was already
    # chosen (same branch route_after_eligibility takes above).
    g.add_conditional_edges(
        "escalate_eligibility", route_after_escalation,
        {"recommend_specialist": "recommend_specialist", "book_real_appointment": "book_real_appointment"},
    )
    g.add_edge("recommend_specialist", "await_specialist_approval")
    g.add_edge("await_specialist_approval", "schedule_appointment")
    g.add_edge("schedule_appointment", "summarize_for_specialist")
    g.add_edge("book_real_appointment", "summarize_for_specialist")
    g.add_edge("summarize_for_specialist", "notify")
    g.add_edge("notify", END)

    return g.compile(checkpointer=checkpointer)


_compiled_graph: Optional[Any] = None
_checkpointer: Optional[Any] = None


async def init_graph(checkpointer) -> None:
    """Called once from `app/main.py`'s lifespan against the real Postgres
    checkpointer. Lifespan never runs under httpx's `ASGITransport` (tests),
    so pytest instead monkeypatches `get_compiled_graph` directly against a
    per-test SQLite checkpointer — see `tests/conftest.py`. Also stashes the
    raw checkpointer itself so the Phase 9 assistant graphs (built per-request,
    not once at startup — see app/agents/assistant_graph.py) can reuse the
    same open Postgres connection instead of opening a second one."""
    global _compiled_graph, _checkpointer
    _checkpointer = checkpointer
    _compiled_graph = build_graph(checkpointer)


def get_compiled_graph() -> Any:
    if _compiled_graph is None:
        raise RuntimeError("Agent graph not initialized — call init_graph() during app startup")
    return _compiled_graph


def get_checkpointer() -> Any:
    if _checkpointer is None:
        raise RuntimeError("Checkpointer not initialized — call init_graph() during app startup")
    return _checkpointer
