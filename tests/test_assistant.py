"""Phase 9 — the conversational assistant. `settings.llm_enabled` is forced
off for every test (see conftest's `force_stub_llm`), so `build_assistant_graph`
always returns `None` here and the FAQ-fallback path is what's actually
exercised — the real tool-calling ReAct agent path (and the cross-patient
tool-call refusal it's meant to guarantee) is verified in the manual
Postgres + real-Groq smoke test instead, the same split already used for
`llm_rank_candidates` in Phase 6."""
from httpx import AsyncClient
from langchain_core.messages import HumanMessage

from app.agents.assistant_graph import (
    KNOWLEDGE_BASE_TOOLS,
    ROLE_TOOL_ALLOWLIST,
    _fetch_kb_prompt_guidance,
    resolve_role_for_tools,
)
from app.api.routes.ai.assistant import faq_fallback
from app.main import app


async def test_chat_requires_authentication(test_client: AsyncClient):
    response = await test_client.post("/assistant/chat", json={"message": "hi", "session_id": "s1"})
    assert response.status_code == 401


async def test_chat_falls_back_to_faq_when_no_llm_configured(test_client: AsyncClient, auth_headers):
    response = await test_client.post(
        "/assistant/chat", headers=auth_headers,
        json={"message": "What's the status of my referral?", "session_id": "s1"},
    )
    assert response.status_code == 200
    assert "status" in response.json()["reply"].lower()


async def test_chat_returns_friendly_message_instead_of_500_when_the_agent_errors(
    test_client: AsyncClient, auth_headers, monkeypatch
):
    """Previously any exception during graph.ainvoke (e.g. exactly the
    tool-call schema validation failure the anyOf/null bug caused — see
    test_no_assistant_tool_param_has_a_nullable_schema) bubbled up as a raw,
    unhandled 500 — "Sorry, something went wrong: Internal Server Error"
    with no way to recover except reloading. Now the chat endpoint catches
    it and keeps the conversation alive instead."""

    class _FailingGraph:
        async def ainvoke(self, *args, **kwargs):
            raise RuntimeError("simulated tool-call validation failure")

    async def _fake_build_assistant_graph(role, token):
        return _FailingGraph()

    monkeypatch.setattr("app.api.routes.ai.assistant.build_assistant_graph", _fake_build_assistant_graph)

    response = await test_client.post(
        "/assistant/chat", headers=auth_headers,
        json={"message": "any referral pending", "session_id": "s1"},
    )
    assert response.status_code == 200
    assert "sorry" in response.json()["reply"].lower()


def test_faq_fallback_matches_known_keywords():
    assert "GET /referral/requests" in faq_fallback("What's my referral status?")
    assert "upload" in faq_fallback("How do I upload a document?").lower()


def test_faq_fallback_default_for_unmatched_message():
    assert "offline mode" in faq_fallback("what's the weather like today?")


def test_resolve_role_for_tools_prefers_most_privileged():
    assert resolve_role_for_tools({"patient"}) == "patient"
    assert resolve_role_for_tools({"pcp"}) == "pcp"
    assert resolve_role_for_tools({"pcp", "care_coordinator"}) == "care_coordinator"
    assert resolve_role_for_tools({"specialist", "patient"}) == "specialist"


def test_resolve_role_for_tools_defaults_to_patient_for_unrecognized_role():
    assert resolve_role_for_tools(set()) == "patient"
    assert resolve_role_for_tools({"totally_made_up_role"}) == "patient"


def test_resolve_role_for_tools_recognizes_admin_and_payer_admin():
    """Both were missing from _ROLE_PRECEDENCE entirely — a real bug (not a
    scoping gap) where an admin or payer_admin caller silently fell back to
    the most restrictive `patient` tools/prompt, producing confusing/wrong
    assistant answers for those roles. admin outranks everything;
    payer_admin sits below the clinical/coordination roles."""
    assert resolve_role_for_tools({"admin"}) == "admin"
    assert resolve_role_for_tools({"payer_admin"}) == "payer_admin"
    assert resolve_role_for_tools({"admin", "care_coordinator"}) == "admin"
    assert resolve_role_for_tools({"payer_admin", "patient"}) == "payer_admin"


def test_tool_allowlist_excludes_unscoped_single_record_routes():
    """No role should ever get a raw id-based get_patient/get_appointment/
    get_medical_record/get_doctor tool — the assistant surface only exposes
    self-scoped (get_my_patient_context) or list-scoped/aggregate
    (get_patient_context, list_appointments, list_medical_records) reads, so
    the model never needs to guess or be handed a specific record id."""
    for role, tools in ROLE_TOOL_ALLOWLIST.items():
        assert "get_patient" not in tools, role
        assert "get_appointment" not in tools, role
        assert "get_medical_record" not in tools, role
        assert "get_doctor" not in tools, role


def test_patient_context_tools_scoped_correctly_per_role():
    """patient gets only the self-scoped get_my_patient_context (never an
    id-based patient-context tool it could point at someone else); every
    staff role scoped through record_scope.py (pcp/specialist/
    care_coordinator/doctor/admin) gets the id-based staff tools instead;
    payer_admin gets neither — it holds no patient/appointment/
    medical_record permission at all (app/core/seed.py)."""
    assert ROLE_TOOL_ALLOWLIST["patient"] & {"get_patient_context", "list_appointments", "list_medical_records"} == set()
    assert "get_my_patient_context" in ROLE_TOOL_ALLOWLIST["patient"]
    for role in ("pcp", "specialist", "care_coordinator", "doctor", "admin"):
        assert {"get_patient_context", "list_appointments", "list_medical_records"} <= ROLE_TOOL_ALLOWLIST[role], role
        assert "get_my_patient_context" not in ROLE_TOOL_ALLOWLIST[role], role
    assert ROLE_TOOL_ALLOWLIST["payer_admin"] & {
        "get_patient_context", "list_appointments", "list_medical_records", "get_my_patient_context"
    } == set()


def test_no_assistant_tool_param_has_a_nullable_schema():
    """The actual bug a patient hit asking the assistant "any referral
    pending" / "list_referrals" q param rejecting `null`: fastapi_mcp
    injects a contradictory top-level `type` onto any query param whose
    schema is `anyOf: [X, null]` (i.e. any `Optional[X] = None` FastAPI
    param) — the resulting tool schema requires the value be BOTH
    `anyOf [X, null]` AND `type: X`, so an LLM's normal `null` (its way of
    saying "omit this optional filter") gets rejected as "expected X, but
    got null". Fixed for every currently-exposed tool by giving each such
    param a concrete, type-matching default (see the comments on
    list_referrals/list_appointments/list_medical_records/list_slots/
    list_availability). This test inspects the REAL generated MCP tool
    schemas (not just today's fixed routes) so a future PR that adds a new
    `Optional[X] = None` query param to an assistant-exposed route fails
    here instead of shipping a live assistant 500."""
    from fastapi_mcp.openapi.convert import convert_openapi_to_mcp_tools

    exposed_tool_names = set().union(*ROLE_TOOL_ALLOWLIST.values())
    tools, _ = convert_openapi_to_mcp_tools(app.openapi())
    exposed_tools = [t for t in tools if t.name in exposed_tool_names]
    assert exposed_tools, "expected at least one exposed tool to check"

    offenders = []
    for tool in exposed_tools:
        for prop_name, prop_schema in (tool.inputSchema.get("properties") or {}).items():
            if "anyOf" in prop_schema:
                offenders.append(f"{tool.name}.{prop_name}")
    assert not offenders, f"nullable (anyOf) MCP tool params found — will reject a legitimate null: {offenders}"


def test_knowledge_base_tool_available_to_every_role():
    """search_policy_knowledge_base is non-PHI, first-party reference
    content, explicitly "available to all" by design (see KNOWLEDGE_BASE_TOOLS
    in assistant_graph.py) — unlike every other tool set in this allowlist,
    it isn't withheld from any role."""
    assert KNOWLEDGE_BASE_TOOLS == {"search_policy_knowledge_base"}
    for role, tools in ROLE_TOOL_ALLOWLIST.items():
        assert KNOWLEDGE_BASE_TOOLS <= tools, role


class _FakeKBPromptClient:
    """Stands in for MultiServerMCPClient.get_prompt for
    _fetch_kb_prompt_guidance — doesn't touch a real MCP server, same
    granularity as tests/agent_fakes.py's FakeMCPTool."""

    def __init__(self, *, raises: bool = False):
        self._raises = raises

    async def get_prompt(self, server_name, prompt_name, arguments=None):
        if self._raises:
            raise RuntimeError("simulated knowledge-base server outage")
        text = f"[{prompt_name}] " + (str(arguments) if arguments else "no-args")
        return [HumanMessage(content=text)]


async def test_fetch_kb_prompt_guidance_fetches_both_real_prompt_templates():
    """Confirms both knowledge_base/main.py prompt templates
    (explain_referral_process, compare_policies) are actually fetched via
    client.get_prompt and folded into one string — not hardcoded copies of
    the template text living in assistant_graph.py itself."""
    guidance = await _fetch_kb_prompt_guidance(_FakeKBPromptClient())
    assert "[explain_referral_process]" in guidance
    assert "[compare_policies]" in guidance


async def test_fetch_kb_prompt_guidance_degrades_gracefully_when_kb_server_unreachable():
    """A knowledge-base outage must not raise out of build_assistant_graph —
    same never-a-500 posture as every other AI-adjacent fallback here
    (ADR-005) — it should just contribute no extra guidance."""
    guidance = await _fetch_kb_prompt_guidance(_FakeKBPromptClient(raises=True))
    assert guidance == ""


def test_only_care_coordinator_gets_the_timeline_and_analytics_tools():
    """Both routes are exactly as scoped as tools every role already has
    (get_referral_timeline shares get_referral's visibility gate;
    get_referral_analytics_summary needs analytics:view, which only
    care_coordinator holds among the roles in this allowlist) — added here
    specifically, not to BASE_REFERRAL_TOOLS, per the de-siloing pass."""
    assert "get_referral_timeline" in ROLE_TOOL_ALLOWLIST["care_coordinator"]
    assert "get_referral_analytics_summary" in ROLE_TOOL_ALLOWLIST["care_coordinator"]
    for role in ("patient", "pcp", "specialist"):
        assert "get_referral_timeline" not in ROLE_TOOL_ALLOWLIST[role]
        assert "get_referral_analytics_summary" not in ROLE_TOOL_ALLOWLIST[role]
