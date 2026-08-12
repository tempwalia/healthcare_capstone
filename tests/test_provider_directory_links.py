"""ProviderDirectoryLink — mapping a mock provider-directory candidate onto a
real platform Doctor, optionally, at resume time. See
app/models/provider_directory_link.py and
app/api/routes/ai/referral_workflow.py::resume_workflow."""
from httpx import AsyncClient

from tests.test_referral import _grant_role
from tests.test_referral_workflow_agents import _submit_referral


async def _submit_to_awaiting_approval(test_client, test_session, test_user_data, auth_headers, test_doctor_data, **kwargs):
    created = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123", reason="Persistent lower back pain", **kwargs,
    )
    state = (await test_client.get(f"/referral-workflow/{created['id']}/state", headers=auth_headers)).json()
    candidate_id = state["specialist_candidates"][0]["doctor_id"]
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    return created, candidate_id


async def test_resume_with_platform_doctor_id_sets_specialist_id_creates_link_and_appointment(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    created, candidate_id = await _submit_to_awaiting_approval(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data
    )
    specialist_data = {**test_doctor_data, "email": "specialist@example.com", "license_number": "MD444444"}
    specialist = (await test_client.post("/doctors/", json=specialist_data, headers=auth_headers)).json()

    resumed = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id, "platform_doctor_id": specialist["id"]},
        headers=auth_headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "scheduled"

    fetched = await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)
    assert fetched.json()["specialist_id"] == specialist["id"]

    links = (await test_client.get("/referral-workflow/provider-links", headers=auth_headers)).json()
    assert any(link["external_doctor_id"] == candidate_id and link["doctor_id"] == specialist["id"] for link in links)

    appointments = (
        await test_client.get(f"/appointments/?doctor_id={specialist['id']}", headers=auth_headers)
    ).json()
    assert any(a["referral_id"] == created["id"] for a in appointments["items"])


async def test_resume_reuses_existing_link_for_the_same_external_doctor_id(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    created, candidate_id = await _submit_to_awaiting_approval(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data
    )
    specialist_data = {**test_doctor_data, "email": "specialist2@example.com", "license_number": "MD333333"}
    specialist = (await test_client.post("/doctors/", json=specialist_data, headers=auth_headers)).json()

    first = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id, "platform_doctor_id": specialist["id"]},
        headers=auth_headers,
    )
    assert first.status_code == 200

    # A second referral to the same mock candidate (same reason -> same
    # specialty -> same deterministic ranking), no platform_doctor_id given
    # this time — should auto-reuse the mapping from the first resume.
    second_patient = (await test_client.post(
        "/patients/",
        json={
            "first_name": "Sam", "last_name": "Lee", "email": "sam.lee@example.com",
            "phone": "+15550003333", "date_of_birth": "1988-03-03", "gender": "male",
            "insurance_provider": "Acme Health", "insurance_policy_number": "ACME-991123",
        },
        headers=auth_headers,
    )).json()
    second_response = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": second_patient["id"],
            "referring_doctor_id": created["referring_doctor_id"],
            "request_date": "2026-08-06",
            "reason": "Persistent lower back pain",
        },
        headers=auth_headers,
    )
    assert second_response.status_code == 202
    second_created = second_response.json()
    second_state = (
        await test_client.get(f"/referral-workflow/{second_created['id']}/state", headers=auth_headers)
    ).json()
    second_candidate_id = second_state["specialist_candidates"][0]["doctor_id"]
    assert second_candidate_id == candidate_id

    second_resume = await test_client.post(
        f"/referral-workflow/{second_created['id']}/resume",
        json={"doctor_id": second_candidate_id},
        headers=auth_headers,
    )
    assert second_resume.status_code == 200

    second_fetched = await test_client.get(f"/referral/requests/{second_created['id']}", headers=auth_headers)
    assert second_fetched.json()["specialist_id"] == specialist["id"]


async def test_resume_without_mapping_leaves_specialist_id_null_and_creates_no_appointment(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    """Regression guard: today's default path (no platform_doctor_id, no
    prior link) must behave exactly as it did before this feature existed."""
    created, candidate_id = await _submit_to_awaiting_approval(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data
    )
    resumed = await test_client.post(
        f"/referral-workflow/{created['id']}/resume",
        json={"doctor_id": candidate_id},
        headers=auth_headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "scheduled"

    fetched = await test_client.get(f"/referral/requests/{created['id']}", headers=auth_headers)
    assert fetched.json()["specialist_id"] is None

    appointments = (await test_client.get("/appointments/", headers=auth_headers)).json()
    assert all(a.get("referral_id") != created["id"] for a in appointments["items"])
