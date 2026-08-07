"""Phase 9 — the conversational assistant. `settings.llm_enabled` is forced
off for every test (see conftest's `force_stub_llm`), so `build_assistant_graph`
always returns `None` here and the FAQ-fallback path is what's actually
exercised — the real tool-calling ReAct agent path (and the cross-patient
tool-call refusal it's meant to guarantee) is verified in the manual
Postgres + real-Groq smoke test instead, the same split already used for
`llm_rank_candidates` in Phase 6."""
from httpx import AsyncClient

from app.agents.assistant_graph import ROLE_TOOL_ALLOWLIST, resolve_role_for_tools
from app.api.routes.ai.assistant import faq_fallback


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
    assert resolve_role_for_tools({"payer_admin"}) == "patient"


def test_patient_tool_allowlist_excludes_unscoped_routes():
    """The base allowlist must never include the unscoped patient/doctor/
    appointment/medical-record routes — see the scoping note in
    app/agents/assistant_graph.py."""
    for role, tools in ROLE_TOOL_ALLOWLIST.items():
        assert "get_patient" not in tools, role
        assert "get_appointment" not in tools, role
        assert "list_appointments" not in tools, role
        assert "get_medical_record" not in tools, role
        assert "get_doctor" not in tools, role


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
