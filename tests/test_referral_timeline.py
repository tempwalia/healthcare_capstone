"""Workstream B — GET /referral/requests/{id}/timeline reads back the
durable outbox_events trail (app/models/outbox.py) that was already being
written on every status change, just never surfaced by a route."""
from httpx import AsyncClient

from tests.test_referral import _grant_role
from tests.test_referral_workflow_agents import _submit_referral, _upload_required_documents


async def test_timeline_reflects_full_referral_progression(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
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

    response = await test_client.get(f"/referral/requests/{eligible['id']}/timeline", headers=auth_headers)
    assert response.status_code == 200
    events = response.json()

    event_types = [e["event_type"] for e in events]
    # intake_node re-runs once per document upload while still
    # awaiting_documents (see app/api/routes/referral.py's re-trigger note),
    # writing a fresh referral.status.changed each time — so this asserts
    # the milestones happened in the right relative order, not an exact
    # replay count.
    assert event_types[0] == "referral.submitted"
    assert event_types[-1] == "referral.appointment.scheduled"
    assert event_types.count("referral.status.changed") >= 1
    assert event_types.index("referral.eligibility.verified") < event_types.index("referral.specialist.recommended")
    assert event_types.index("referral.specialist.recommended") < event_types.index("referral.appointment.scheduled")
    assert all(events[i]["created_at"] <= events[i + 1]["created_at"] for i in range(len(events) - 1))

    submitted = events[0]
    assert submitted["label"] == "Referral Submitted"
    assert submitted["payload"]["referral_id"] == eligible["id"]

    scheduled = events[-1]
    assert scheduled["label"] == "Appointment Scheduled"


async def test_timeline_is_visibility_scoped_like_the_referral_itself(
    test_client: AsyncClient, test_session, test_user_data, auth_headers, test_doctor_data
):
    denied = await _submit_referral(
        test_client, test_session, test_user_data, auth_headers, test_doctor_data,
        insurance_policy_number="UNKNOWN-000000",
        reason="Chest pain",
    )
    own = await test_client.get(f"/referral/requests/{denied['id']}/timeline", headers=auth_headers)
    assert own.status_code == 200
    assert any(e["event_type"] == "referral.eligibility.denied" for e in own.json())

    # A bare registered user with no role and no linkage to this referral.
    await test_client.post(
        "/auth/register",
        json={"email": "outsider@example.com", "username": "outsider", "password": "testpassword123"},
    )
    login = await test_client.post(
        "/auth/login", data={"username": "outsider", "password": "testpassword123"},
    )
    outsider_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    other = await test_client.get(f"/referral/requests/{denied['id']}/timeline", headers=outsider_headers)
    assert other.status_code == 403  # no referral:view_own/view_all at all
