"""Phase 11 — read-only analytics aggregation over real referral data."""
from httpx import AsyncClient

from tests.test_referral import _grant_role
from tests.test_referral_workflow_agents import _submit_referral, _upload_required_documents


async def test_analytics_requires_permission(test_client: AsyncClient, auth_headers):
    response = await test_client.get("/analytics/referrals/summary", headers=auth_headers)
    assert response.status_code == 403


async def test_referral_summary_reflects_real_data(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data, test_patient_data
):
    eligible = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123",
        reason="Persistent lower back pain, suspected herniated disc",
    )
    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    state = (await test_client.get(f"/referral-workflow/{eligible['id']}/state", headers=auth_headers)).json()
    candidate_id = state["specialist_candidates"][0]["doctor_id"]
    await test_client.post(
        f"/referral-workflow/{eligible['id']}/resume", json={"doctor_id": candidate_id}, headers=auth_headers,
    )

    # A second patient referred to the same doctor, with no insurance info on
    # file (per `test_patient_data`) — denies eligibility, same as
    # `test_referral.py`'s equivalent case.
    other_patient = (await test_client.post(
        "/patients/", json={**test_patient_data, "email": "denied.patient@example.com"}, headers=auth_headers
    )).json()
    denied = (await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": other_patient["id"],
            "referring_doctor_id": eligible["referring_doctor_id"],
            "request_date": "2026-08-06",
            "reason": "Persistent lower back pain",
        },
        headers=auth_headers,
    )).json()
    await _upload_required_documents(test_client, auth_headers, denied["id"])

    response = await test_client.get("/analytics/referrals/summary", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["by_status"].get("scheduled", 0) >= 1
    assert body["by_status"].get("eligibility_denied", 0) >= 1
    assert body["avg_time_to_schedule_hours"] >= 0
    assert body["eligibility_denial_rate"] > 0
    assert any(s["specialty"] == "Orthopedics" for s in body["top_specialties_requested"])
    assert isinstance(body["delay_risk_referrals"], int)
