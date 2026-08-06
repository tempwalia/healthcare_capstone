"""Phases 6-7 — the real LangGraph referral workflow (orchestration,
document processing/extraction), exercised end-to-end against the fake MCP
tool layer in tests/agent_fakes.py (see conftest's `agent_graph_test_setup`).
"""
from httpx import AsyncClient

from mock_systems.notification_mock.main import (
    sent_messages as notification_sent_messages,
)
from tests.test_referral import _grant_role, _link_doctor_to_user, _link_patient_to_user


async def _upload_required_documents(
    test_client: AsyncClient, auth_headers, referral_id: int,
    *, letter_text: str = "Routine referral letter.",
    imaging_text: str = "MRI imaging report: unremarkable.",
):
    """Uploads one document matching each of `intake_node`'s
    REQUIRED_DOC_TYPES keyword heuristics (filename-based). Each upload
    re-triggers the background workflow while the referral is still
    `awaiting_documents` — by the time the second call returns, the workflow
    has re-run `intake_node` and (if both types are now present) moved on."""
    await test_client.post(
        f"/referral/requests/{referral_id}/documents", headers=auth_headers,
        files={"file": ("referral_letter.txt", letter_text.encode(), "text/plain")},
    )
    return await test_client.post(
        f"/referral/requests/{referral_id}/documents", headers=auth_headers,
        files={"file": ("recent_imaging_report.txt", imaging_text.encode(), "text/plain")},
    )


async def _submit_referral(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
    *, insurance_policy_number: str, reason: str,
    letter_text: str = "Routine referral letter.",
    imaging_text: str = "MRI imaging report: unremarkable.",
):
    await _grant_role(test_session, test_user_data["username"], "pcp")

    patient_data = {
        "first_name": "Jamie",
        "last_name": "Rivera",
        "email": "jamie.rivera@example.com",
        "phone": "+15550001111",
        "date_of_birth": "1985-04-12",
        "gender": "female",
        "insurance_provider": "Acme Health",
        "insurance_policy_number": insurance_policy_number,
    }
    patient = (await test_client.post("/patients/", json=patient_data, headers=auth_headers)).json()
    doctor = (await test_client.post("/doctors/", json=test_doctor_data, headers=auth_headers)).json()
    await _link_doctor_to_user(test_session, doctor["id"], test_user_data["username"])

    # A separate account linked to the patient, so notify_node has a
    # `user_id` to message once the appointment is booked.
    await test_client.post(
        "/auth/register",
        json={"email": "jamie.login@example.com", "username": "jamie_login", "password": "testpassword123"},
    )
    await _link_patient_to_user(test_session, patient["id"], "jamie_login")

    response = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": patient["id"],
            "referring_doctor_id": doctor["id"],
            "request_date": "2026-08-06",
            "reason": reason,
        },
        headers=auth_headers,
    )
    assert response.status_code == 202
    created = response.json()

    await _upload_required_documents(
        test_client, auth_headers, created["id"], letter_text=letter_text, imaging_text=imaging_text,
    )
    return created


async def test_eligible_referral_pauses_for_specialist_approval_then_resumes_to_scheduled(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain, suspected herniated disc",
    )

    fetched = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert fetched["status"] == "awaiting_specialist_approval"

    state = (await test_client.get(f"/referral-workflow/{created['id']}/state", headers=auth_headers)).json()
    assert len(state["specialist_candidates"]) >= 1
    assert all("reasons" in c and "score" in c for c in state["specialist_candidates"])
    candidate_id = state["specialist_candidates"][0]["doctor_id"]

    # a pcp-only user (no referral:approve) cannot resume the pause
    denied = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id}, headers=auth_headers,
    )
    assert denied.status_code == 403

    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    resumed = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id}, headers=auth_headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "scheduled"

    final = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert final["status"] == "scheduled"
    # `specialist_id` is a real FK into our own `doctors` table; the mock
    # provider directory's doctor_id is a separate synthetic ID space with no
    # row there, so it deliberately isn't written back onto the referral.
    assert final["specialist_id"] is None

    notes = (await test_client.get(f"/referral/requests/{created['id']}/notes", headers=auth_headers)).json()
    assert len(notes) == 1

    assert notification_sent_messages[-1]["channel"] == "email"
    assert "confirmed" in notification_sent_messages[-1]["message"]


async def test_scheduling_with_no_available_slots_marks_delayed(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data, monkeypatch,
):
    """Phase 8: `scheduling_node`'s "no slots within target" branch — forces
    the mock scheduling system's own availability generator to return zero
    slots (a real, existing knob on that system, not a hack around it), so
    the referral lands on `scheduling_delayed` instead of `scheduled`."""
    from mock_systems.scheduling_mock import main as scheduling_mock_main

    monkeypatch.setattr(scheduling_mock_main, "MAX_SLOTS_RETURNED", 0)

    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain, suspected herniated disc",
    )
    state = (await test_client.get(f"/referral-workflow/{created['id']}/state", headers=auth_headers)).json()
    candidate_id = state["specialist_candidates"][0]["doctor_id"]

    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    resumed = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id}, headers=auth_headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "scheduling_delayed"

    final = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert final["status"] == "scheduling_delayed"


async def test_ineligible_referral_denies_without_pausing(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="NOT-A-REAL-PLAN",
        reason="Persistent lower back pain",
    )

    fetched = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert fetched["status"] == "eligibility_denied"

    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    response = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": 88}, headers=auth_headers,
    )
    assert response.status_code == 409
