"""GET /referral/requests/ops-queue — the care coordinator worklist: referrals
awaiting a specialist pick, eligibility-denied referrals, and scheduled
referrals with no outcome recorded yet. See app/api/routes/referral.py's
list_ops_queue_referrals."""
from httpx import AsyncClient

from tests.test_referral import _grant_role
from tests.test_referral_outcome import _complete_referral_to_scheduled
from tests.test_referral_workflow_agents import _submit_referral


async def test_ops_queue_requires_referral_approve_permission(
    test_client: AsyncClient, test_session, test_user_data, auth_headers
):
    await _grant_role(test_session, test_user_data["username"], "patient")
    response = await test_client.get("/referral/requests/ops-queue", headers=auth_headers)
    assert response.status_code == 403


async def test_ops_queue_includes_awaiting_approval_and_eligibility_denied_but_not_submitted(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    awaiting = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="ACME-991123", reason="Awaiting-approval fixture",
    )

    # A second referral from the same already-pcp-linked account, built
    # directly rather than via a second _submit_referral call — that helper
    # hardcodes a single "jamie_login"/"jamie.rivera@example.com" identity
    # and re-grants the pcp role unconditionally, so calling it twice in one
    # test collides on both.
    denied_patient = (await test_client.post(
        "/patients/",
        json={
            "first_name": "Alex", "last_name": "Kim", "email": "alex.kim@example.com",
            "phone": "+15550002222", "date_of_birth": "1979-02-02", "gender": "male",
            "insurance_provider": "Acme Health", "insurance_policy_number": "NOT-A-REAL-PLAN",
        },
        headers=auth_headers,
    )).json()
    denied_response = await test_client.post(
        "/referral/requests/",
        json={
            "patient_id": denied_patient["id"],
            "referring_doctor_id": awaiting["referring_doctor_id"],
            "request_date": "2026-08-06",
            "reason": "Eligibility-denied fixture",
        },
        headers=auth_headers,
    )
    assert denied_response.status_code == 202
    denied = denied_response.json()

    await _grant_role(test_session, test_user_data["username"], "care_coordinator")
    queue = (await test_client.get("/referral/requests/ops-queue", headers=auth_headers)).json()
    queue_ids = {item["id"] for item in queue["items"]}
    queue_statuses = {item["id"]: item["status"] for item in queue["items"]}

    assert awaiting["id"] in queue_ids
    assert queue_statuses[awaiting["id"]] == "awaiting_specialist_approval"
    assert denied["id"] in queue_ids
    assert queue_statuses[denied["id"]] == "eligibility_denied"


async def test_ops_queue_includes_scheduled_referral_with_no_outcome_yet(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    scheduled = await _complete_referral_to_scheduled(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
    )

    queue = (await test_client.get("/referral/requests/ops-queue", headers=auth_headers)).json()
    queue_ids = {item["id"] for item in queue["items"]}
    assert scheduled["id"] in queue_ids

    outcome_response = await test_client.post(
        f"/referral/requests/{scheduled['id']}/outcome", headers=auth_headers, json={"symptoms": "back pain"},
    )
    assert outcome_response.status_code == 202

    queue_after = (await test_client.get("/referral/requests/ops-queue", headers=auth_headers)).json()
    assert scheduled["id"] not in {item["id"] for item in queue_after["items"]}
