"""Post-referral consult outcome capture (symptoms/diagnosis/prescription)
and the resulting whole-care-journey completion summary."""
from httpx import AsyncClient

from tests.test_referral import _grant_role
from tests.test_referral_workflow_agents import _submit_referral


async def _complete_referral_to_scheduled(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
) -> dict:
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
    assert resumed.json()["status"] == "scheduled"
    return created


async def test_recording_outcome_requires_permission(test_client: AsyncClient, auth_headers):
    response = await test_client.post(
        "/referral/requests/9999/outcome", headers=auth_headers, json={"symptoms": "back pain"},
    )
    assert response.status_code == 403


async def test_recording_outcome_completes_referral_and_generates_summary(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )

    response = await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=auth_headers,
        json={
            "symptoms": "Lower back pain, limited mobility",
            "diagnosis": "Lumbar disc herniation",
            "prescription": "Naproxen 500mg twice daily",
            "follow_up_notes": "Follow up in 4 weeks; consider physical therapy",
        },
    )
    assert response.status_code == 202
    assert response.json()["referral_request_id"] == created["id"]

    final = (await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)).json()
    assert final["status"] == "completed"

    outcome = (
        await test_client.get(f"/referral/requests/{created['id']}/outcome", headers=auth_headers)
    ).json()
    assert outcome["interaction_summary"] is not None
    assert "Naproxen" in outcome["interaction_summary"]
    assert "Lumbar disc herniation" in outcome["interaction_summary"]


async def test_recording_outcome_twice_conflicts(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )

    first = await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=auth_headers, json={"symptoms": "back pain"},
    )
    assert first.status_code == 202

    second = await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=auth_headers, json={"symptoms": "back pain again"},
    )
    assert second.status_code == 409


async def test_outcome_visibility_excludes_patient(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data,
):
    created = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )
    await test_client.post(
        f"/referral/requests/{created['id']}/outcome", headers=auth_headers,
        json={"symptoms": "back pain", "diagnosis": "disc herniation", "prescription": "NSAIDs"},
    )

    # the referring doctor / care coordinator account (test_user_data) can see it
    staff_view = await test_client.get(f"/referral/requests/{created['id']}/outcome", headers=auth_headers)
    assert staff_view.status_code == 200

    # the patient (linked as "jamie_login" inside _submit_referral) can see
    # the referral itself via referral:view_own, but not its outcome
    await _grant_role(test_session, "jamie_login", "patient")
    patient_login = await test_client.post(
        "/auth/login", data={"username": "jamie_login", "password": "testpassword123"},
    )
    patient_headers = {"Authorization": f"Bearer {patient_login.json()['access_token']}"}

    can_see_referral = await test_client.get(f"/referral/requests/{created['id']}", headers=patient_headers)
    assert can_see_referral.status_code == 200

    cannot_see_outcome = await test_client.get(
        f"/referral/requests/{created['id']}/outcome", headers=patient_headers
    )
    assert cannot_see_outcome.status_code == 403
