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

# Everything about a patient (medical records, appointments/scheduling,
# consulting-doctor/care-team history) beyond bare referral status — the
# "de-siloing" pass's app/services/patient_context.py aggregation, plus the
# two standalone lists it's built from. Safe to expose now: every route
# behind these tool names is scoped through record_scope.py's
# appointment_visibility_filter/medical_record_visibility_filter/
# patient_visibility_filter (the Phase-9-era "unscoped routes" gap these
# were once withheld for — see git history on this constant — was fixed
# separately and is no longer a reason to hold them back).
STAFF_PATIENT_TOOLS: Set[str] = {"get_patient_context", "list_appointments", "list_medical_records"}
# The patient-role equivalent: self-scoped, no patient_id parameter at all,
# so there's never an id for the model to guess or substitute.
PATIENT_SELF_TOOLS: Set[str] = {"get_my_patient_context"}

ROLE_TOOL_ALLOWLIST: dict[str, Set[str]] = {
    "patient": BASE_REFERRAL_TOOLS | PATIENT_SELF_TOOLS,
    "pcp": BASE_REFERRAL_TOOLS | STAFF_PATIENT_TOOLS,
    "specialist": BASE_REFERRAL_TOOLS | STAFF_PATIENT_TOOLS,
    "care_coordinator": BASE_REFERRAL_TOOLS
    | STAFF_PATIENT_TOOLS
    | {
        "get_workflow_state", "list_slots", "list_availability",
        # Both added for the "de-siloing" pass: same visibility gate as
        # get_referral (_get_scoped_referral) and the same analytics:view
        # permission the coordinator role already holds — no new scoping
        # surface, just more of what this role can already see through the
        # dashboard, now reachable conversationally too.
        "get_referral_timeline", "get_referral_analytics_summary",
    },
    # Same referral:view_all breadth as care_coordinator (this role isn't
    # tied to any one referral either — see app/core/seed.py) plus
    # get_workflow_state to check whether a referral still has a pending
    # specialist-approval step to pick up.
    "doctor": BASE_REFERRAL_TOOLS | STAFF_PATIENT_TOOLS | {"get_workflow_state"},
    # Read-only oversight, no clinical access: payer_admin's only granted
    # permissions are referral:view_all and analytics:view (app/core/seed.py)
    # — no patient/appointment/medical_record permission at all, so
    # STAFF_PATIENT_TOOLS is deliberately withheld even though the tool
    # names alone wouldn't leak anything a payer shouldn't see (the routes
    # would just 403); scoping the assistant's tool surface to match this
    # role's real permission set, not just what happens to succeed.
    "payer_admin": BASE_REFERRAL_TOOLS | {"get_referral_analytics_summary", "get_referral_timeline"},
    # admin:* bypasses every permission check at the route level, so there's
    # no narrower "correct" tool surface to compute — give it the union of
    # every other role's tools (the same breadth an admin already has by
    # clicking through every page of the dashboard).
    "admin": BASE_REFERRAL_TOOLS
    | STAFF_PATIENT_TOOLS
    | {
        "get_workflow_state", "list_slots", "list_availability",
        "get_referral_timeline", "get_referral_analytics_summary",
    },
}

ROLE_SYSTEM_PROMPTS: dict[str, str] = {
    "patient": (
        "You are a helpful assistant for patients of the Intelligent Care Coordination platform. "
        "Answer questions about the caller's own referrals — status, documents on file, and any "
        "specialist notes — and, using get_my_patient_context, their own upcoming/past appointments, "
        "medical records, insurance on file, and care team (which doctors are involved and in what "
        "role). That tool always resolves to the caller's own linked record; never call it with, or "
        "invent, an id. You can only ever see this specific patient's data; never speculate about "
        "or claim to know anything about another patient. If get_my_patient_context 404s, the "
        "caller's account isn't linked to a patient record yet — tell them to ask an admin to link "
        "one, don't guess at data. Keep answers warm, clear, and avoid unexplained clinical jargon."
    ),
    "pcp": (
        "You are an assistant for primary care providers using the Intelligent Care Coordination "
        "platform. Help track the status of referrals the caller has submitted — eligibility, "
        "specialist recommendations, documents, and notes — and, using get_patient_context(patient_id) "
        "and list_appointments/list_medical_records, look up a specific patient's appointments, medical "
        "records, and care team when the caller gives you a patient id or you find one via a referral. "
        "You can only see referrals tied to the caller's own provider account, and patient data for "
        "patients you're actually a party to (their PCP, an appointment, or a record) — a 403/404 means "
        "the caller isn't a party to that patient, not a bug."
    ),
    "specialist": (
        "You are an assistant for specialist physicians using the Intelligent Care Coordination "
        "platform. Help the caller review referral status, attached documents, and prior-history notes "
        "for referrals they're party to, and, using get_patient_context(patient_id) and "
        "list_appointments/list_medical_records, look up a specific patient's appointments, medical "
        "records, and care team when the caller gives you a patient id or you find one via a referral."
    ),
    "care_coordinator": (
        "You are an assistant for care coordination staff using the Intelligent Care Coordination "
        "platform. Help the caller track referral status platform-wide, including specialist "
        "recommendation reasoning, workflow state, and scheduling availability, to support "
        "coordinating patient care. You can also pull a specific referral's full milestone-by-milestone "
        "timeline, a platform-wide referral analytics summary (status breakdown, delay-risk count, "
        "average time to schedule, eligibility denial rate, top specialties), and — via "
        "get_patient_context(patient_id)/list_appointments/list_medical_records — any patient's full "
        "unified record (appointments, medical records, insurance, care team) for coordination purposes."
    ),
    "doctor": (
        "You are an assistant for the doctor role on the Intelligent Care Coordination platform — a "
        "POC stand-in for a specialist actually seeing patients, not tied to any one referral. Help the "
        "caller find referrals still awaiting a specialist decision, review a referral's documents, "
        "prior notes, and workflow state before they select a specialist or record a consult outcome, "
        "and pull up a patient's full record via get_patient_context(patient_id)/"
        "list_appointments/list_medical_records when a patient id comes up."
    ),
    "payer_admin": (
        "You are an assistant for payer-side staff using the Intelligent Care Coordination platform. "
        "Your access is read-only and referral/analytics-focused — you can check referral status, "
        "documents, and specialist notes platform-wide, a referral's milestone timeline, and the "
        "referral analytics summary (status breakdown, delay-risk, average time to schedule, denial "
        "rate, top specialties). You do NOT have access to patient appointments or medical records — "
        "if asked for those, explain that's outside the payer_admin role's access rather than guessing "
        "or trying another tool."
    ),
    "admin": (
        "You are an assistant for platform administrators using the Intelligent Care Coordination "
        "platform. You have the broadest available view: referral status and documents platform-wide, "
        "workflow state, scheduling availability, the referral analytics summary, and any patient's "
        "full unified record (appointments, medical records, care team) via "
        "get_patient_context(patient_id). Use this for oversight, troubleshooting, and answering "
        "questions about the state of the platform — you are not any one patient, so never answer a "
        "'my referrals' style question as if you were; ask which patient or referral they mean."
    ),
}

# Appended to every role prompt below (not duplicated 7 times) — a tool
# result is a JSON object/array meant for the model to read and narrate, not
# to echo back. Without this, some models (observed with the Groq-hosted
# model this app defaults to) reply with the raw tool payload — a field list
# or a JSON blob — instead of a written answer, especially for
# multi-field results like get_my_patient_context or get_referral_analytics_summary.
_FORMATTING_INSTRUCTIONS = (
    "\n\nFormatting: always answer in plain, natural, conversational sentences — never paste raw JSON, "
    "a Python/dict-style object, or an unexplained code block. Turn structured tool results into prose or "
    "a short bulleted/numbered list with clear labels (e.g. \"Your next appointment is Aug 17 at 9:30am with "
    "Dr. Rao\", not the field names or fenced JSON straight from the tool). Use markdown formatting "
    "(bold, bullet points, headings) only where it genuinely helps readability, not to reproduce a raw payload."
)
ROLE_SYSTEM_PROMPTS = {role: prompt + _FORMATTING_INSTRUCTIONS for role, prompt in ROLE_SYSTEM_PROMPTS.items()}

# Most-privileged first: a user with multiple roles gets the broadest
# matching tool/prompt configuration. `admin` leads (admin:* outranks every
# other permission); `payer_admin` sits after the clinical/coordination
# roles since it's narrower than any of them, but still ahead of `patient`.
_ROLE_PRECEDENCE = ["admin", "care_coordinator", "doctor", "specialist", "pcp", "payer_admin", "patient"]


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
