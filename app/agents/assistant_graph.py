from typing import Any, Optional, Set

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

from app.agents import graph as agent_graph
from app.agents.llm import StubChatModel, get_chat_model
from app.core.config import settings

# Read-only, referral-domain tools only. Every one of these routes goes
# through app/api/routes/referral.py::_get_scoped_referral (or the same
# scoping reused by app/api/routes/ai/referral_workflow.py) — the platform's
# real per-user visibility boundary, not just a UI-layer convenience.
#
# Deliberately NOT exposing get_patient/get_doctor/get_appointment/
# list_appointments/get_medical_record here even though they exist as MCP
# tools too: those routes only check `get_current_active_user` (any
# authenticated user), with no patient/doctor ownership scoping at all — a
# pre-existing gap from Phases 0-5 that predates this assistant. Handing an
# LLM a tool that can fetch *any* patient's record by ID would defeat the
# entire point of a "role-specific, no cross-patient leakage" assistant, so
# the tool surface stays limited to what's actually scoped today. Fixing
# those routes' authorization is a separate, larger change outside Phase 9.
BASE_REFERRAL_TOOLS: Set[str] = {
    "get_referral", "list_referrals", "list_referral_documents", "list_specialist_notes",
}

ROLE_TOOL_ALLOWLIST: dict[str, Set[str]] = {
    "patient": BASE_REFERRAL_TOOLS,
    "pcp": BASE_REFERRAL_TOOLS,
    "specialist": BASE_REFERRAL_TOOLS,
    "care_coordinator": BASE_REFERRAL_TOOLS | {"get_workflow_state", "list_slots", "list_availability"},
}

ROLE_SYSTEM_PROMPTS: dict[str, str] = {
    "patient": (
        "You are a helpful assistant for patients of the Intelligent Care Coordination platform. "
        "Answer questions about the caller's own referrals only — status, documents on file, and any "
        "specialist notes. You can only ever see this specific patient's data; never speculate about "
        "or claim to know anything about another patient. Keep answers warm, clear, and avoid "
        "unexplained clinical jargon."
    ),
    "pcp": (
        "You are an assistant for primary care providers using the Intelligent Care Coordination "
        "platform. Help track the status of referrals the caller has submitted — eligibility, "
        "specialist recommendations, documents, and notes. You can only see referrals tied to the "
        "caller's own provider account."
    ),
    "specialist": (
        "You are an assistant for specialist physicians using the Intelligent Care Coordination "
        "platform. Help the caller review referral status, attached documents, and prior-history notes "
        "for referrals they're party to."
    ),
    "care_coordinator": (
        "You are an assistant for care coordination staff using the Intelligent Care Coordination "
        "platform. Help the caller track referral status platform-wide, including specialist "
        "recommendation reasoning, workflow state, and scheduling availability, to support "
        "coordinating patient care."
    ),
}

# Most-privileged first: a user with multiple roles gets the broadest
# matching tool/prompt configuration.
_ROLE_PRECEDENCE = ["care_coordinator", "specialist", "pcp", "patient"]


def resolve_role_for_tools(granted_role_names: Set[str]) -> str:
    for role in _ROLE_PRECEDENCE:
        if role in granted_role_names:
            return role
    return "patient"  # most restrictive default for an unrecognized/absent role


async def build_assistant_graph(role: str, auth_token: str) -> Optional[Any]:
    """Built fresh per chat request, not cached at module/startup scope like
    the referral workflow graph — the whole point is that the MCP client
    carries *this specific calling user's* bearer token, so every tool call
    it makes runs through the platform's normal auth as that real user, not
    some shared system identity. `fastapi_mcp`'s default
    `headers=["authorization"]` config (unchanged in app/main.py) forwards
    it through to the underlying route.

    Returns `None` before ever touching MCP/network *or the checkpointer* if
    no LLM is configured (ADR-005) — the chat route falls back to a
    deterministic FAQ responder, and pytest (which forces the stub LLM
    globally) never needs `agent_graph.get_checkpointer()` to be initialized.
    """
    llm = get_chat_model("assistant")
    if isinstance(llm, StubChatModel):
        return None

    checkpointer = agent_graph.get_checkpointer()
    client = MultiServerMCPClient({
        "platform": {
            "url": f"{settings.api_base_url}/mcp",
            "transport": "streamable_http",
            "headers": {"Authorization": f"Bearer {auth_token}"},
        }
    })
    all_tools = await client.get_tools()
    allowed = ROLE_TOOL_ALLOWLIST.get(role, ROLE_TOOL_ALLOWLIST["patient"])
    tools = [tool for tool in all_tools if tool.name in allowed]

    return create_react_agent(
        llm, tools, checkpointer=checkpointer,
        prompt=ROLE_SYSTEM_PROMPTS.get(role, ROLE_SYSTEM_PROMPTS["patient"]),
    )
